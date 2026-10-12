"""The real 'Dealer List' header row (from TMS_Delight_09-10-2026.xlsx). Row data is synthetic."""

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db import models as m
from app.workbooks.ingest import import_workbook
from tests.integration.test_workbook_ingest import CFG
from tests.xlsx_helpers import make_xlsx

REAL_HEADERS = [
    "Date", "Sr. No.", "New Region", "Dealer Code", "Dealer Name", "Division Code", "City",
    "Hub Locations", "SPOC - Name", "SPOC - Contact", "Address1", "Address2", "Address3",
    "Pincode", "State", "Contacted", "Readiness", "Remark", "Installation Planned Date",
    "Installation Status", "Phase", "Expecting Timeline for Installation", "Dispatch Date",
    "Status", "Albersons Remakrs",
]  # fmt: skip


def mk(div: str, city: str, readiness: str, **kw: object) -> list[object]:
    dealer = kw.get("dealer", div.split("-")[0] if "-" in div else None)
    return [
        None, 1, "South", dealer, "Test Motors Pvt. Ltd.", div, city, None, "Person A",
        9000000000, "Addr 1", None, None, "682001", "Kerala", kw.get("contacted", "Yes"),
        readiness, None, kw.get("plan"), kw.get("inst"), "Phase 1", kw.get("timeline"),
        kw.get("dispatch"), kw.get("status"), None,
    ]  # fmt: skip


def test_real_headers_all_map_and_dealer_code_comes_from_division(
    db: Session, tmp_path: Path
) -> None:
    rows = [
        REAL_HEADERS,
        mk(
            "3000060-Sv&Pa-Derikplaza-DetRik",
            "Thiruvananthapuram",
            "Ready To Deploy",
            plan="09-10-2026",
        ),
        mk(
            "3085780-Sv&Pa-Nedumangad-NedUma",
            "Thiruvananthapuram",
            "Not Ready",
            timeline="Next week",
        ),
        mk("300C030-Sv&Pa-Pantheeran-SreGom", "Kozhikode", "Ready To Deploy", dispatch="1-10-2026"),
        mk("300A100-Sv&Pa-Ernakulam-GokUla", "Ernakulam", "Not Ready", status="Dispatched"),
        mk("New Setup", "Kollam", "Not Ready"),
        mk("300A42-Sv&Pa-Bad-BadBad", "Kannur", "Ready To Deploy"),
        # blank Dealer Code cell: derived from the division prefix; odd "Contacted" text is flagged
        mk(
            "300A200-Sv&Pa-Xtown-Abc",
            "Xtown",
            "Done",
            dealer=None,
            inst="Completed",
            contacted="standalone bodyshop",
        ),
    ]
    p = make_xlsx(tmp_path / "TMS_Delight_09-10-2026.xlsx", {"Dealer List": rows})
    res = import_workbook(
        db,
        p,
        filename=p.name,
        posted_at=datetime(2026, 10, 9, 10, tzinfo=UTC),
        cfg=CFG,
        derived_dir=tmp_path / "d",
    )
    db.flush()
    imp = res.import_
    assert imp is not None and imp.role == "MASTER"
    sheet = db.scalar(select(m.WorkbookSheet))
    assert sheet is not None and sheet.header_row == 1
    cols = sheet.column_mapping["columns"]
    unknown = [c["header_raw"] for c in cols if c["method"] != "exact"]
    assert unknown == []  # all 25 real headers map exactly
    canon = {c["header_raw"]: c["canonical"] for c in cols}
    assert canon["Hub Locations"] == "hub_city" and canon["Pincode"] == "pin"
    assert canon["Installation Planned Date"] == "planned_install_date"
    assert canon["Expecting Timeline for Installation"] == "expected_timeline"
    assert canon["Status"] == "dispatch_status" and canon["Albersons Remakrs"] == "client_remarks"
    dealers = {
        e.canonical_key for e in db.scalars(select(m.Entity).where(m.Entity.type == "dealer_code"))
    }
    assert dealers == {"3000060", "3085780", "300C030", "300A100", "300A42", "300A200"}
    assert canon["New Region"] == "region" and canon["Dealer Code"] == "dealer_code"
    inst = db.scalars(select(m.Observation).where(m.Observation.dimension == "installation")).all()
    assert [(o.value, o.polarity, o.source_kind) for o in inst] == [
        ("completed", "positive", "workbook_cell")
    ]
    unm = db.scalars(select(m.ReviewItem).where(m.ReviewItem.kind == "unmapped_status_value")).all()
    assert [u.proposed_value["raw"] for u in unm] == ["standalone bodyshop"]
    bad = db.scalars(select(m.ReviewItem).where(m.ReviewItem.kind == "invalid_value")).all()
    assert [b.subject_refs["field"] for b in bad] == ["dealer_code"]  # only 300A42 is invalid
    free = db.scalar(select(m.WorkbookRow).where(m.WorkbookRow.row_number == 3))
    assert free is not None and free.normalized["expected_timeline__text"] == "Next week"
    prov = db.scalars(select(m.Entity).where(m.Entity.provisional.is_(True))).all()
    assert any(e.canonical_key.startswith("newsetup||kollam|") for e in prov)


def test_master_with_implementation_plan_sheet_stays_master_and_reimport(
    db: Session, tmp_path: Path
) -> None:
    from sqlalchemy import func

    from app.workbooks.ingest import delete_workbook_import

    dealer = [REAL_HEADERS] + [
        mk(
            f"300A{i:03d}-Sv&Pa-Town{i}-Abc",
            f"Town{i}",
            "Ready To Deploy" if i % 2 else "Not Ready",
        )
        for i in range(12)
    ]
    # the real master also has a bigger sheet named "implementation Plan" and a pivot "Summary"
    plan = [REAL_HEADERS] + [
        mk(f"300B{i:03d}-Sv&Pa-Plan{i}-Abc", "Plan", "Not Ready") for i in range(30)
    ]
    p = make_xlsx(
        tmp_path / "TMS_Delight_09-10-2026.xlsx",
        {"Summary": [["Row Labels", "Count of Division Code"], ["x", 1]],
         "implementation Plan": plan, "Dealer List": dealer},
    )  # fmt: skip
    at = datetime(2026, 10, 9, 10, tzinfo=UTC)
    res = import_workbook(db, p, filename=p.name, posted_at=at, cfg=CFG, derived_dir=tmp_path / "d")
    db.flush()
    imp = res.import_
    assert imp is not None and imp.role == "MASTER"  # not IMPLEMENTATION_PLAN
    assert res.stats["rows"] == 12  # the preferred "Dealer List" sheet is the primary one
    n_obs = db.scalar(select(func.count()).select_from(m.Observation))
    assert n_obs and n_obs > 0
    # re-processing: delete the import, then import the same file again
    delete_workbook_import(db, imp, "test")
    assert db.scalar(select(func.count()).select_from(m.WorkbookImport)) == 0
    assert db.scalar(select(func.count()).select_from(m.Observation)) == 0
    again = import_workbook(
        db, p, filename=p.name, posted_at=at, cfg=CFG, derived_dir=tmp_path / "d"
    )
    assert again.created is True and again.import_ is not None and again.import_.role == "MASTER"
    assert db.scalar(select(func.count()).select_from(m.AuditLog)) == 1


def test_master_import_makes_a_bounded_number_of_queries(
    db: Session, tmp_path: Path, engine: Engine
) -> None:
    """Regression guard: the first version made ~9,400 queries for 1,180 rows, which took many
    minutes against a remote database. Query count must not grow with the number of rows."""
    from sqlalchemy import event

    rows = [REAL_HEADERS] + [
        mk(
            f"300A{i:03d}-Sv&Pa-Town{i}-Abc",
            f"Town{i}",
            "Done" if i % 2 else "Pending",
            inst="Planned",
        )
        for i in range(300)
    ]
    p = make_xlsx(tmp_path / "TMS_Delight_09-10-2026.xlsx", {"Dealer List": rows})
    count = [0]

    def tick(*_a: object, **_k: object) -> None:
        count[0] += 1

    event.listen(engine, "before_cursor_execute", tick)
    try:
        res = import_workbook(
            db, p, filename=p.name, posted_at=datetime(2026, 10, 9, 10, tzinfo=UTC), cfg=CFG,
            derived_dir=tmp_path / "d",
        )  # fmt: skip
        db.flush()
    finally:
        event.remove(engine, "before_cursor_execute", tick)
    assert res.stats["master"]["divisions"] == 300
    assert count[0] < 120, f"{count[0]} statements for 300 rows: per-row queries are back"
