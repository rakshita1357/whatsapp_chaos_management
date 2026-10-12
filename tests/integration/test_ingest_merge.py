from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from app.db import models as m
from app.ingest.adapters.whatsapp_export import alt_md, alt_txt, android_native
from app.ingest.merge import persist

G = "Test Group"
NATIVE = (
    '09/10/2026, 1:27 pm - Asha created group "Test Group"\n'
    "09/10/2026, 1:27 pm - Asha: Please share the report\n"
    "09/10/2026, 1:28 pm - Ravi: Tv installed\n"
    "09/10/2026, 1:29 pm - Ravi: secret\n"
)
ALT_MD = """# WhatsApp Chat Export: Test Group

## October 9, 2026

[1:27 PM] **Asha:** Please share the report
[1:28 PM] **Ravi:** [Forwarded] Tv installed
> _Asha: Please share the report_
[1:29 PM] **Ravi:** _This message was deleted_
"""


def _clean(engine: Engine) -> None:
    with Session(engine) as s:
        for t in (
            m.AuditLog,
            m.Observation,
            m.EventEvidence,
            m.Event,
            m.Attachment,
            m.MessageVersion,
            m.SourceMessage,
            m.ReviewItem,
            m.Import,
            m.Person,
            m.Group,
        ):
            s.execute(delete(t))
        s.commit()


def _count(s: Session, model: type) -> int:
    return s.scalar(select(func.count()).select_from(model)) or 0


def test_idempotent_double_import_and_cross_format_merge(engine: Engine) -> None:
    _clean(engine)
    with Session(engine) as s:
        _, st1 = persist(s, android_native.parse(NATIVE, G), G, adapter="t")
        s.commit()
        assert st1.created == 3 and _count(s, m.SourceMessage) == 3
        g = s.scalar(select(m.Group))
        assert g is not None and g.created_on is not None
        _, st2 = persist(s, android_native.parse(NATIVE, G), G, adapter="t")
        s.commit()
        assert st2.created == 0 and st2.merged == 3 and _count(s, m.SourceMessage) == 3
        # alt_md: forwarded flag + quote link enrich; deleted placeholder hard-deletes "secret"
        _, st3 = persist(s, alt_md.parse(ALT_MD), G, adapter="t")
        s.commit()
        assert st3.created == 0 and st3.quote_links == 1 and st3.deleted == 1
        msgs = {x.normalized_text: x for x in s.scalars(select(m.SourceMessage))}
        assert "secret" not in msgs
        tv = msgs["Tv installed"]
        assert tv.forwarded is True and tv.reply_method == "quote" and tv.reply_to_message_id
        audit = s.scalar(select(m.AuditLog).where(m.AuditLog.action == "source_message_deleted"))
        assert audit is not None and "secret" not in str(audit.after)
        # re-import the deleting export: idempotent, no new tombstone for the removed message
        persist(s, alt_md.parse(ALT_MD), G, adapter="t")
        s.commit()
        assert _count(s, m.SourceMessage) == 3  # 2 real + 1 hash-only tombstone
        # re-importing the OLD native export must not resurrect the deleted message
        persist(s, android_native.parse(NATIVE, G), G, adapter="t")
        s.commit()
        assert "secret" not in {x.normalized_text for x in s.scalars(select(m.SourceMessage))}


def test_second_precision_upgrade(engine: Engine) -> None:
    _clean(engine)
    with Session(engine) as s:
        persist(s, android_native.parse("09/10/2026, 1:28 pm - Ravi: ok\n", G), G, adapter="t")
        r = alt_txt.parse("[10/9/26, 1:28:42 PM] Ravi: ok\n", G)
        persist(s, r, G, adapter="t")
        s.commit()
        msg = s.scalar(select(m.SourceMessage))
        assert msg is not None and msg.sent_at_precision == "second" and msg.sent_at.second == 42
        assert _count(s, m.SourceMessage) == 1


def test_unknown_group_review_and_tombstone(engine: Engine) -> None:
    _clean(engine)
    t = "09/10/2026, 1:28 pm - Ravi: This message was deleted\n"
    with Session(engine) as s:
        persist(s, android_native.parse(t, G), G, adapter="t")
        persist(s, android_native.parse(t, G), G, adapter="t")
        s.commit()
        assert _count(s, m.SourceMessage) == 1
        tomb = s.scalar(select(m.SourceMessage))
        assert tomb is not None and tomb.message_type == "deleted" and tomb.raw_text is None
        assert s.scalar(select(m.ReviewItem).where(m.ReviewItem.kind == "unknown_group_client"))


def test_ambiguous_date_order_commits_nothing(engine: Engine) -> None:
    _clean(engine)
    with Session(engine) as s:
        imp, _ = persist(s, android_native.parse("05/05/2026, 1:00 pm - A: x\n", G), G, adapter="t")
        s.commit()
        assert imp.status == "NEEDS_REVIEW" and _count(s, m.SourceMessage) == 0
        assert s.scalar(select(m.ReviewItem).where(m.ReviewItem.kind == "date_order_ambiguous"))


def test_edit_updates_text_and_versions(engine: Engine) -> None:
    _clean(engine)
    with Session(engine) as s:
        persist(
            s,
            android_native.parse("09/10/2026, 1:28 pm - Ravi: not installed\n", G),
            G,
            adapter="t",
        )
        persist(
            s,
            android_native.parse(
                "09/10/2026, 1:28 pm - Ravi: installed <This message was edited>\n", G
            ),
            G,
            adapter="t",
        )
        s.commit()
        assert _count(s, m.SourceMessage) == 1
        v = s.scalar(select(m.MessageVersion))
        assert v is not None and v.before_text == "not installed" and v.after_text == "installed"


def test_attachments_linked_hashed_and_deleted_blob_kept_if_shared(
    engine: Engine, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    from app.core.blobstore import BlobStore

    _clean(engine)
    store = BlobStore(tmp_path / "b")
    f = tmp_path / "IMG-1.jpg"
    f.write_bytes(b"jpgdata")
    t = (
        "09/10/2026, 1:28 pm - Ravi: IMG-1.jpg (file attached)\ncaption\n"
        "09/10/2026, 1:29 pm - Ravi: IMG-2.jpg (file attached)\n"
    )
    with Session(engine) as s:
        _, st = persist(
            s,
            android_native.parse(t, G),
            G,
            adapter="t",
            files={"IMG-1.jpg": f, "extra.jpg": f},
            store=store,
        )
        s.commit()
        assert st.attachments == 2 and st.missing_files == 1
        avail = {a.export_filename: a.availability for a in s.scalars(select(m.Attachment))}
        assert avail == {"IMG-1.jpg": "present", "IMG-2.jpg": "missing_file"}
        imp = s.scalar(select(m.Import))
        assert imp is not None and any("not referenced" in w for w in imp.warnings)
