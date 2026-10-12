from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import ROOT
from app.db import models as m
from app.workbooks.ingest import diff_imports, import_workbook
from app.workbooks.settings import load_workbook_config
from tests.xlsx_helpers import HEADERS, make_xlsx, row

CFG = load_workbook_config(ROOT / "config")
CFG.settings["master_min_rows"] = 5  # tiny fixtures stand in for the ~1000-row master
T1 = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
T2 = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)
READY = "Ready To Deploy"


def master_rows() -> list[list[object]]:
    return [
        HEADERS,
        row(1, "3007530", "3007530-Sv&Pa-Chokidhani-RdrMtr", "Chokidhani", READY, pin="800001"),
        row(2, "3007530", "3007530-Sv&Pa-Gaya-RdrMtr", "Gaya", "Hold"),
        row(3, "300A490", "300A490 -Sv&Pa- Himatnagar", "Himatnagar", READY),
        row(4, "300A640", "300A640-Sv-Chittorgarh", "Chittorgarh", READY),
        row(5, "300A640", "300A640-Sv-Chittorgarh", "Chittorgarh", "Not Ready"),
        row(6, "3007531", "3007531-Sv-Biharsarif", "Biharsarif", READY),
        row(7, "3007531", "3007531-Sv-Biharsharif", "Biharsharif", READY),
        row(8, "300A42", "300A42-Sv-Nowhere", "Nowhere", READY),
        row(9, "3007530", "New Setup", "Patna", "Not Ready"),
        row(10, "3007530", "3007530-Sv-Hajipur", "Hajipur", "Not Ready", plan="10-10-206"),
    ]


def load_master(
    db: Session,
    tmp_path: Path,
    name: str = "TMS_Delight_09-10-2026.xlsx",
    rows: list[list[object]] | None = None,
    at: datetime = T1,
) -> m.WorkbookImport:
    p = make_xlsx(
        tmp_path / name,
        {"Dealer List": rows or master_rows(), "Pivot": [["Row Labels", "Count of x"]]},
    )
    res = import_workbook(
        db, p, filename=name, posted_at=at, posted_by="Asha", cfg=CFG, derived_dir=tmp_path / "d"
    )
    db.flush()
    assert res.import_ is not None
    return res.import_


def kinds(db: Session) -> dict[str, int]:
    return dict(
        db.execute(select(m.ReviewItem.kind, func.count()).group_by(m.ReviewItem.kind)).all()
    )


def test_master_load(db: Session, tmp_path: Path) -> None:
    imp = load_master(db, tmp_path)
    assert imp.role == "MASTER" and imp.opened_via == "plain" and imp.version_no == 1
    sheets = {s.name: s.kind for s in db.scalars(select(m.WorkbookSheet))}
    assert sheets == {"Dealer List": "data_table", "Pivot": "pivot_summary"}
    divs = {
        e.canonical_key: e for e in db.scalars(select(m.Entity).where(m.Entity.type == "division"))
    }
    assert "300a490-sv&pa-himatnagar" in divs  # whitespace stripped + casefold
    dealers = {
        e.canonical_key for e in db.scalars(select(m.Entity).where(m.Entity.type == "dealer_code"))
    }
    assert {"3007530", "300A490", "300A640", "3007531", "300A42"} == dealers
    chok = divs["3007530-sv&pa-chokidhani-rdrmtr"]
    assert chok.parent_id is not None and chok.attributes["city"] == "Chokidhani"
    prov = [e for e in divs.values() if e.provisional]
    assert len(prov) == 1 and prov[0].canonical_key.startswith("newsetup|3007530|patna|")  # 8.5
    k = kinds(db)
    assert k["duplicate_division_conflict"] == 1  # INC-24: Chittorgarh
    assert k["possible_duplicate_entity"] == 1  # INC-23: Biharsarif/Biharsharif
    assert k["invalid_value"] >= 2  # dealer code 300A42 and the 10-10-206 date (INC-44)
    assert k["unmapped_status_value"] == 1  # "Hold"
    assert k["entity_match_ambiguous"] == 1  # New Setup
    # both Chittorgarh rows are kept and both produce readiness observations on one entity
    chit = divs["300a640-sv-chittorgarh"]
    vals = sorted(
        o.value or ""
        for o in db.scalars(
            select(m.Observation).where(
                m.Observation.entity_id == chit.id, m.Observation.dimension == "readiness"
            )
        )
    )
    assert vals == ["not_ready", "ready"]
    # Sr. No. is kept raw but never part of normalized keys
    r1 = db.scalar(select(m.WorkbookRow).where(m.WorkbookRow.row_number == 2))
    assert r1 is not None and r1.raw["Sr. No."] == 1 and "sr_no" in r1.normalized


def test_same_sha_is_same_version_and_unknown_header_reviewed(db: Session, tmp_path: Path) -> None:
    rows = master_rows()
    rows[0] = [*HEADERS, "Brand New Column"]
    for r in rows[1:]:
        r.append("x")
    imp = load_master(db, tmp_path, rows=rows)
    again = import_workbook(
        db, tmp_path / "TMS_Delight_09-10-2026.xlsx", filename="copy.xlsx", posted_at=T2, cfg=CFG
    )
    assert again.created is False and again.import_ is not None and again.import_.id == imp.id
    assert db.scalar(select(func.count()).select_from(m.WorkbookImport)) == 1
    unk = db.scalars(select(m.ReviewItem).where(m.ReviewItem.kind == "unknown_header")).all()
    assert [u.subject_refs["header"] for u in unk] == ["Brand New Column"]
    row2 = db.scalar(select(m.WorkbookRow).where(m.WorkbookRow.row_number == 2))
    assert row2 is not None and row2.raw["Brand New Column"] == "x"  # INC-43: raw kept


def test_version_order_follows_posting_time_not_filename(db: Session, tmp_path: Path) -> None:
    newer = load_master(db, tmp_path, "TMS_Delight_09-10-2026.xlsx", at=T2)
    older_rows = master_rows()
    older_rows[1][6] = "Not Ready"
    older = load_master(db, tmp_path, "TMS_Delight_09-10-2026-1.xlsx", rows=older_rows, at=T1)
    db.refresh(newer)
    assert newer.family_key == older.family_key
    assert (older.version_no, newer.version_no) == (1, 2)  # INC-13: "-1" file is the older one
    d = db.scalar(select(m.WorkbookDiff))
    assert d is not None and d.from_import_id == older.id and d.to_import_id == newer.id


def test_diff_categories(db: Session, tmp_path: Path) -> None:
    v1 = master_rows()
    v1[3][8] = datetime(2026, 11, 1)  # Himatnagar planned date (timeline)
    v1[2][10] = "Pending"  # Gaya dispatch status (dispatch)
    v1[1][7] = datetime(2026, 3, 4)  # Chokidhani site ready date, will be day/month swapped
    a = load_master(db, tmp_path, "TMS_Delight_09-10-2026.xlsx", rows=v1, at=T1)
    v2 = [list(r) for r in v1]
    v2[0] = [h if h != "Pin" else "Pincode" for h in v2[0]]  # header rename
    v2[3][8] = datetime(2026, 11, 15)
    v2[2][10] = "Dispatched"
    v2[1][7] = datetime(2026, 4, 3)
    v2[10][5] = 800001  # Hajipur: same pin, different cell type -> format-only
    v2 = [r for r in v2 if r[2] != "3007531-Sv-Biharsarif"]  # one removed
    v2.append(row(11, "300A490", "300A490-Sv-New", "Newtown", READY))  # one added
    b = load_master(db, tmp_path, "TMS_Delight_10-10-2026.xlsx", rows=v2, at=T2)
    d = db.scalar(select(m.WorkbookDiff).where(m.WorkbookDiff.to_import_id == b.id))
    assert d is not None
    s = d.summary
    assert (s["added"], s["removed"]) == (1, 1)  # +Newtown, -Biharsarif
    assert s["conflict"] == 1  # Chittorgarh duplicate, in both versions
    assert s["changed_rows"] == 3
    assert s["changed_by_group"] == {"timeline": 2, "dispatch": 1}
    assert s["format_only"] == 1
    assert s["possible_dm_swap_corrections"] == 1
    assert {"field": "pin", "old_header": "Pin", "new_header": "Pincode"} in s["header_renames"]
    items = db.scalars(select(m.WorkbookDiffItem).where(m.WorkbookDiffItem.diff_id == d.id)).all()
    swap = [i for i in items if "possible_dm_swap_correction" in i.annotations]
    assert len(swap) == 1 and swap[0].column == "site_ready_date"
    # a changed diff does not touch Sr. No. (positional)
    assert all(i.column != "sr_no" for i in items)
    again = diff_imports(db, a, b, CFG)
    assert again.summary["added"] == s["added"]
