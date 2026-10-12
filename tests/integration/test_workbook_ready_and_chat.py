from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.blobstore import BlobStore
from app.db import models as m
from app.ingest.service import import_path
from app.workbooks.ingest import import_workbook, ready_delta
from tests.integration.test_workbook_ingest import (
    CFG,
    HEADERS,
    READY,
    T1,
    T2,
    load_master,
    make_xlsx,
    master_rows,
    row,
)


def ready_file(db: Session, tmp: Path, name: str, keys: list[tuple[str, str, str]], at: datetime):  # type: ignore[no-untyped-def]
    rows = [HEADERS] + [row(i, d, div, c, READY) for i, (d, div, c) in enumerate(keys, 1)]
    p = make_xlsx(tmp / name, {"Ready List": rows})
    res = import_workbook(db, p, filename=name, posted_at=at, cfg=CFG, derived_dir=tmp / "d")
    db.flush()
    assert res.import_ is not None
    return res.import_


CHOK = ("3007530", "3007530-Sv&Pa-Chokidhani-RdrMtr", "Chokidhani")
HIM = ("300A490", "300A490-Sv&Pa-Himatnagar", "Himatnagar")
BIH = ("3007531", "3007531-Sv-Biharsarif", "Biharsarif")
GAYA = ("3007530", "3007530-Sv&Pa-Gaya-RdrMtr", "Gaya")


def test_ready_list_observations_removal_and_delta(db: Session, tmp_path: Path) -> None:
    master = load_master(db, tmp_path)
    v1 = ready_file(db, tmp_path, "Ready_List_09-10-2026.xlsx", [CHOK, HIM, BIH, GAYA], T1)
    assert v1.role == "READY_LIST"
    listed = db.scalars(
        select(m.Observation).where(m.Observation.dimension == "readiness_listed")
    ).all()
    assert len(listed) == 4 and all(o.value == "true" for o in listed)
    # the 'Hold' (unmapped) Gaya row has no readiness; the ready list says Ready
    # next version drops Himatnagar and Gaya (INC-47)
    v2 = ready_file(db, tmp_path, "Ready_List_10-10-2026.xlsx", [CHOK, BIH], T2)
    assert (v1.version_no, v2.version_no) == (1, 2)
    gone = db.scalars(
        select(m.Observation).where(
            m.Observation.dimension == "readiness_listed", m.Observation.value == "false"
        )
    ).all()
    assert len(gone) == 2
    assert all(o.source_ref["reason"] == "unknown" for o in gone)
    ents = {db.get(m.Entity, o.entity_id).canonical_key for o in gone}  # type: ignore[union-attr]
    assert ents == {"300a490-sv&pa-himatnagar", "3007530-sv&pa-gaya-rdrmtr"}
    # master-ready vs ready list v1: master has Chokidhani, Himatnagar, Chittorgarh(dup, excluded),
    # Biharsarif, Biharsharif, Nowhere as ready; Gaya is listed but not ready in master
    delta = ready_delta(db, master, v1, CFG)
    assert delta == {"newly_ready": 2, "dropped": 1}  # +Biharsharif +Nowhere ; -Gaya


def test_removed_row_links_recent_chat_blocker(db: Session, tmp_path: Path) -> None:
    load_master(db, tmp_path)
    v1 = ready_file(db, tmp_path, "Ready_List_09-10-2026.xlsx", [CHOK, HIM], T1)
    him = db.scalar(select(m.Entity).where(m.Entity.canonical_key == "300a490-sv&pa-himatnagar"))
    assert him is not None
    ev = m.Event(type="issue", entity_id=him.id, opened_at=T2 - timedelta(days=2), job_types=[])
    db.add(ev)
    db.flush()
    ready_file(db, tmp_path, "Ready_List_10-10-2026.xlsx", [CHOK], T2)
    o = db.scalar(select(m.Observation).where(m.Observation.value == "false"))
    assert o is not None and o.source_ref["linked_chat_blockers"] == [str(ev.id)]
    assert v1.version_no == 1


def test_workbooks_in_chat_use_posting_time_for_versions(db: Session, tmp_path: Path) -> None:
    export = tmp_path / "export"
    export.mkdir()
    g = "Tata Delight Meter Core Group"
    (export / f"WhatsApp Chat with {g}.txt").write_text(
        "09/10/2026, 10:00 am - Asha: DOC-001.xlsx (file attached)\n"
        "TMS_Delight_10-10-2026.xlsx\n"
        "10/10/2026, 11:00 am - Asha: DOC-002.xlsx (file attached)\n"
        "TMS_Delight_09-10-2026-1.xlsx\n",
        encoding="utf-8",
    )
    # DOC-001 (posted first) is the file whose NAME looks newer; the name must not decide order
    first = master_rows()
    first[2][6] = "Not Ready"
    make_xlsx(export / "DOC-001.xlsx", {"Dealer List": first})
    make_xlsx(export / "DOC-002.xlsx", {"Dealer List": master_rows()})
    from app.workbooks import ingest

    orig = ingest.load_workbook_config
    ingest.load_workbook_config = lambda *a, **k: CFG  # type: ignore[assignment]
    try:
        res = import_path(db, export, store=BlobStore(tmp_path / "blobs"))
    finally:
        ingest.load_workbook_config = orig
    db.flush()
    imp, _ = res[0]
    assert imp.status == "PROCESSED" and len(imp.stats["workbooks"]) == 2
    wbs = {w.attachment_id: w for w in db.scalars(select(m.WorkbookImport))}
    atts = {a.export_filename: a for a in db.scalars(select(m.Attachment))}
    assert atts["DOC-001.xlsx"].doc_kind == "workbook"
    w1, w2 = wbs[atts["DOC-001.xlsx"].id], wbs[atts["DOC-002.xlsx"].id]
    assert w1.family_key == w2.family_key
    assert (w1.version_no, w2.version_no) == (1, 2)
    assert w1.posted_by == "Asha" and w1.posted_at < w2.posted_at  # type: ignore[operator]
    grp = db.scalar(select(m.Group).where(m.Group.name == g))
    assert grp is not None and grp.client_id is not None  # configured client, no review
    assert (
        db.scalar(select(m.ReviewItem).where(m.ReviewItem.kind == "unknown_group_client")) is None
    )
    assert db.scalar(select(m.WorkbookDiff)) is not None
    assert datetime.now(UTC) > T1
