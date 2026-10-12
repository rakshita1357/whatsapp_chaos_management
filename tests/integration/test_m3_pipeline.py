from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models as m
from app.ingest.adapters.whatsapp_export import android_native
from app.ingest.merge import persist
from app.pipeline.process import process_group
from app.providers.base import ProviderError
from app.providers.recorded import RecordedProvider, RecordingProvider
from tests.m3_helpers import (
    ASHA,
    MARUTI_GROUP,
    RAVI,
    SAM,
    TATA_GROUP,
    ScriptedProvider,
    cls,
    native,
    seed_people,
    seed_site,
)  # fmt: skip

HIM_DIV = "300A490-Sv&Pa-Himatnagar"
CHOK_DIV = "3007530-Sv&Pa-Chokidhani-RdrMtr"
L = "⁨"
R = "⁩"


def clean(s: str) -> str:
    return s.replace(L, "").replace(R, "")  # what the pipeline shows the model (INC-04)


def load(db: Session, group: str, lines: list[tuple[str, str, str]]) -> m.Group:
    persist(db, android_native.parse(native(lines), group), group, adapter="t")
    db.flush()
    g = db.scalar(select(m.Group).where(m.Group.name == group))
    assert g is not None
    return g


def count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


def state(db: Session, entity: m.Entity, dim: str) -> m.EntityState | None:
    return db.scalar(
        select(m.EntityState).where(
            m.EntityState.entity_id == entity.id, m.EntityState.dimension == dim
        )
    )


def test_golden_style_thread_linking_ack_aggregates_and_review(db: Session) -> None:
    seed_people(db)
    him = seed_site(db, "300A490", HIM_DIV, "Himatnagar")
    chok = seed_site(db, "3007530", CHOK_DIV, "Chokidhani")
    t1 = f"{HIM_DIV} not installed, no screen available. @{L}Sam{R} please check"
    t2 = f"{CHOK_DIV} NO TV INSTALLED. @{L}Ravi{R} please check"
    t3 = "Tv installed in lounge"
    t4 = f"{CHOK_DIV} NO TV INSTALLED again"
    t5 = "renovation going on, 10 days"
    t6 = "Tv installed"
    t7 = "Yesterday - 15, today - 9 so far"
    g = load(db, TATA_GROUP, [
        ("14:00", ASHA, t1), ("14:05", RAVI, "Ok checking"), ("14:20", ASHA, t2), ("14:53", RAVI, t3),
        ("16:10", ASHA, t4), ("16:19", RAVI, t5), ("16:21", RAVI, t6), ("17:00", SAM, t7),
    ])  # fmt: skip
    provider = ScriptedProvider({
        clean(t1): cls(clean(t1), status=[("installation", "negative", "not installed", "not installed")],
                issues=[("no_screen_available", "no screen", None, "no screen available")]),
        clean(t2): cls(clean(t2), status=[("installation", "negative", "not installed", "NO TV INSTALLED")]),
        t3: cls(t3, status=[("installation", "positive", "installed", "Tv installed")]),
        t4: cls(t4, status=[("installation", "negative", "not installed", "NO TV INSTALLED")]),
        t5: cls(t5, issues=[("renovation", "renovation", "10 days", "renovation going on")]),
        t6: cls(t6, status=[("installation", "positive", "installed", "Tv installed")]),
        t7: cls(t7, "AGGREGATE_REPORT", claims=[("installations", "yesterday", 15, "="), ("installations", "today", 9, "=")]),
    })  # fmt: skip
    stats = process_group(db, g.id, provider, raise_on_error=True)
    db.flush()
    assert provider.calls == 7 and stats.rule_only == 1  # the ack never reached the model

    # self-identifying messages resolve directly, no link needed
    ev1 = db.scalars(
        select(m.Event).where(m.Event.entity_id == him.id, m.Event.type == "issue")
    ).one()
    assert ev1.link_method == "self_identifying" and ev1.assigned_to_person_id is not None
    # the ack moved the issue open -> acknowledged and did NOT resolve it
    assert ev1.lifecycle_status == "acknowledged"
    assert state(db, him, "installation").value == "not_installed_reported"  # type: ignore[union-attr]
    ack = db.scalar(select(m.SourceMessage).where(m.SourceMessage.normalized_text == "Ok checking"))
    assert ack is not None and ack.intent == "ACKNOWLEDGEMENT" and ack.reply_method == "inferred"

    # 14:53 "Tv installed in lounge": never automatic, Himatnagar ranked first
    lounge = db.scalar(select(m.SourceMessage).where(m.SourceMessage.normalized_text == t3))
    assert lounge is not None and lounge.processing_state == "NEEDS_REVIEW"
    item = db.scalars(
        select(m.ReviewItem).where(
            m.ReviewItem.dedupe_key == f"msg:{lounge.id}:reply_link_ambiguous"
        )
    ).one()
    assert item.candidates[0]["entity_id"] == str(him.id)
    assert item.candidates[0]["score"] > item.candidates[1]["score"]
    ev3 = db.scalars(
        select(m.Event)
        .join(m.EventEvidence, m.EventEvidence.event_id == m.Event.id)
        .where(m.EventEvidence.message_id == lounge.id)
    ).one()
    assert ev3.entity_id is None  # stored with no entity until a human decides

    # 16:19 renovation: review with Chokidhani first, never Himatnagar first
    reno = db.scalar(select(m.SourceMessage).where(m.SourceMessage.normalized_text == t5))
    assert reno is not None
    ri = db.scalars(
        select(m.ReviewItem).where(m.ReviewItem.dedupe_key == f"msg:{reno.id}:reply_link_ambiguous")
    ).one()
    assert ri.candidates[0]["entity_id"] == str(chok.id)

    # 16:21 native-only "Tv installed": must NEVER auto-link to Chokidhani (or anything)
    last = db.scalar(select(m.SourceMessage).where(m.SourceMessage.normalized_text == t6))
    assert last is not None and last.processing_state == "NEEDS_REVIEW"
    ev6 = db.scalars(
        select(m.Event)
        .join(m.EventEvidence, m.EventEvidence.event_id == m.Event.id)
        .where(m.EventEvidence.message_id == last.id)
    ).one()
    assert ev6.entity_id is None and ev6.link_method is None
    assert stats.auto_links == 0 and stats.review_links == 3
    assert state(db, chok, "installation").value == "not_installed_reported"  # type: ignore[union-attr]

    # aggregates are claims, never events
    claims = db.scalars(select(m.AggregateClaim).order_by(m.AggregateClaim.value)).all()
    assert [(c.value, c.scope_date, c.scope_partial_as_of is not None) for c in claims] == [
        (9, date(2026, 10, 9), True),
        (15, date(2026, 10, 8), False),
    ]
    agg_msg = db.scalar(select(m.SourceMessage).where(m.SourceMessage.normalized_text == t7))
    assert agg_msg is not None
    n_ev = db.scalar(
        select(func.count())
        .select_from(m.EventEvidence)
        .where(m.EventEvidence.message_id == agg_msg.id)
    )
    assert n_ev == 0

    # follow-up window from ETA text
    iss = db.scalars(
        select(m.Event).where(
            m.Event.type == "issue", m.Event.payload["issue_type"].astext == "renovation"
        )
    ).one()
    assert iss.follow_up_due_from == date(2026, 10, 19)


def test_auto_link_only_when_assignment_and_ack_agree(db: Session) -> None:
    seed_people(db)
    him = seed_site(db, "300A490", HIM_DIV, "Himatnagar")
    t1 = f"{HIM_DIV} not installed, no screen available. @{L}Ravi{R} please check"
    t3 = "Tv installed in lounge"
    g = load(
        db, TATA_GROUP, [("14:00", ASHA, t1), ("14:05", RAVI, "Ok checking"), ("14:53", RAVI, t3)]
    )
    provider = ScriptedProvider({
        clean(t1): cls(clean(t1), status=[("installation", "negative", "n", "not installed")]),
        t3: cls(t3, status=[("installation", "positive", "installed", "Tv installed")]),
    })  # fmt: skip
    stats = process_group(db, g.id, provider, raise_on_error=True)
    lounge = db.scalar(select(m.SourceMessage).where(m.SourceMessage.normalized_text == t3))
    assert lounge is not None and stats.auto_links == 1 and lounge.processing_state == "PROCESSED"
    ev = db.scalars(
        select(m.Event)
        .join(m.EventEvidence, m.EventEvidence.event_id == m.Event.id)
        .where(m.EventEvidence.message_id == lounge.id)
    ).one()
    assert ev.entity_id == him.id and ev.link_method == "scored" and ev.link_confidence >= 0.8  # type: ignore[operator]
    assert lounge.reply_method == "inferred" and lounge.reply_to_message_id is not None
    assert state(db, him, "installation").value == "installation_reported"  # type: ignore[union-attr]


def test_reprocessing_is_idempotent(db: Session) -> None:
    seed_people(db)
    him = seed_site(db, "300A490", HIM_DIV, "Himatnagar")
    t1 = f"{HIM_DIV} TV laga diya hai"
    t2 = "Ok"
    g = load(db, TATA_GROUP, [("14:00", RAVI, t1), ("14:01", ASHA, t2)])
    provider = ScriptedProvider(
        {
            t1: cls(
                t1, status=[("installation", "positive", "installed", "laga diya")], lang="hinglish"
            )
        }
    )
    process_group(db, g.id, provider, raise_on_error=True)
    snapshot = (
        count(db, m.Event),
        count(db, m.Observation),
        count(db, m.EntityState),
        count(db, m.EventEvidence),
    )
    assert state(db, him, "installation").value == "installation_reported"  # type: ignore[union-attr]
    for _ in range(2):
        process_group(db, g.id, provider, force=True, raise_on_error=True)
        assert (
            count(db, m.Event),
            count(db, m.Observation),
            count(db, m.EntityState),
            count(db, m.EventEvidence),
        ) == snapshot
    skipped = process_group(db, g.id, provider, raise_on_error=True)
    assert skipped.skipped == 2 and skipped.bundles == 0  # processed bundles are left alone


def test_cassette_record_then_replay_makes_no_model_calls(db: Session, tmp_path: Path) -> None:
    seed_people(db)
    seed_site(db, "300A490", HIM_DIV, "Himatnagar")
    t1 = f"{HIM_DIV} not installed"
    g = load(db, TATA_GROUP, [("14:00", RAVI, t1)])
    script = ScriptedProvider(
        {t1: cls(t1, status=[("installation", "negative", "not installed", "not installed")])}
    )
    process_group(db, g.id, RecordingProvider(script, tmp_path), raise_on_error=True)
    cassettes = list(tmp_path.glob("classify_message/*.json"))
    assert len(cassettes) == 1
    before = count(db, m.Observation)
    s = process_group(db, g.id, RecordedProvider(tmp_path), force=True, raise_on_error=True)
    assert s.model_calls == 1 and count(db, m.Observation) == before  # replayed from the cassette


def test_provider_failure_is_retryable_not_fatal(db: Session) -> None:
    seed_people(db)
    seed_site(db, "300A490", HIM_DIV, "Himatnagar")
    g = load(db, TATA_GROUP, [("14:00", RAVI, f"{HIM_DIV} installed"), ("14:01", ASHA, "Ok")])
    s = process_group(db, g.id, ScriptedProvider({}))
    assert s.failed == 1 and "script has no entry" in s.errors[0]
    states = {x.normalized_text: x.processing_state for x in db.scalars(select(m.SourceMessage))}
    assert states[f"{HIM_DIV} installed"] == "FAILED_RETRYABLE" and states["Ok"] == "PROCESSED"
    with pytest.raises(ProviderError):
        process_group(db, g.id, ScriptedProvider({}), raise_on_error=True)


def test_dealer_code_with_many_divisions_is_ambiguous_until_town_names_one(db: Session) -> None:
    seed_people(db)
    a = seed_site(db, "3007530", "3007530-Sv&Pa-Chokidhani-RdrMtr", "Chokidhani")
    seed_site(db, "3007530", "3007530-Sv&Pa-Gaya-RdrMtr", "Gaya")
    t1 = "3007530 not installed"
    t2 = "3007530 Gaya not installed"
    t3 = "3007530 Chokidhani not installed"
    g = load(db, TATA_GROUP, [("10:00", ASHA, t1), ("11:00", ASHA, t2), ("12:00", ASHA, t3)])
    prov = ScriptedProvider({
        t1: cls(t1, status=[("installation", "negative", "n", "not installed")], mentions=[("3007530", "dealer_code")]),
        t2: cls(t2, status=[("installation", "negative", "n", "not installed")], mentions=[("3007530", "dealer_code"), ("Gaya", "town")]),
        t3: cls(t3, status=[("installation", "negative", "n", "not installed")], mentions=[("Chokidhani", "town")]),
    })  # fmt: skip
    process_group(db, g.id, prov, raise_on_error=True)
    amb = db.scalars(select(m.ReviewItem).where(m.ReviewItem.kind == "division_ambiguity")).all()
    assert len(amb) == 1 and len(amb[0].candidates) == 2  # only the message without a town
    ents = {e.entity_id for e in db.scalars(select(m.Event)) if e.entity_id}
    assert len(ents) == 2 and a.id in ents  # Gaya and Chokidhani resolved by town


def test_evidence_makes_installed_with_evidence_and_never_verified(
    db: Session, tmp_path: Path
) -> None:
    from app.core.blobstore import BlobStore

    seed_people(db)
    him = seed_site(db, "300A490", HIM_DIV, "Himatnagar")
    t = f"{HIM_DIV} installed"
    img = tmp_path / "IMG-1.jpg"
    img.write_bytes(b"img")
    lines = (
        "09/10/2026, 2:00 pm - +91 90000 00001: IMG-1.jpg (file attached)\n"
        f"09/10/2026, 2:00 pm - +91 90000 00001: {t}\n"
    )
    persist(db, android_native.parse(lines, TATA_GROUP), TATA_GROUP, adapter="t",
            files={"IMG-1.jpg": img}, store=BlobStore(tmp_path / "b"))  # fmt: skip
    g = db.scalar(select(m.Group))
    assert g is not None
    process_group(
        db,
        g.id,
        ScriptedProvider(
            {t: cls(t, status=[("installation", "positive", "installed", "installed")])}
        ),
        raise_on_error=True,
    )
    st = state(db, him, "installation")
    assert st is not None and st.value == "installed_with_evidence"  # D6: counts as installed
    assert st.value != "verified"  # a photo never verifies; only a human review action does


def test_workbook_vs_chat_conflicts_stale_source_and_ready_but_installed(db: Session) -> None:
    seed_people(db)
    him = seed_site(db, "300A490", HIM_DIV, "Himatnagar")
    wb_time = datetime(2026, 10, 8, 10, tzinfo=UTC)
    for dim, val, pol in (
        ("installation", "completed", "positive"),
        ("readiness", "ready", "positive"),
    ):
        db.add(m.Observation(entity_id=him.id, dimension=dim, value=val, polarity=pol, observed_at=wb_time,
                             effective_date=date(2026, 10, 8), source_kind="workbook_cell",
                             extractor="workbook", extractor_version="1"))  # fmt: skip
    t = f"{HIM_DIV} NO TV INSTALLED"
    g = load(db, TATA_GROUP, [("14:00", RAVI, t)])
    process_group(
        db,
        g.id,
        ScriptedProvider(
            {t: cls(t, status=[("installation", "negative", "n", "NO TV INSTALLED")])}
        ),
        raise_on_error=True,
    )
    st = state(db, him, "installation")
    assert (
        st is not None and st.value == "not_installed_reported" and st.conflict_flag
    )  # chat wins; flagged
    kinds = {r.kind for r in db.scalars(select(m.ReviewItem))}
    assert "stale_source" in kinds  # the older sheet says Completed, newer chat says not installed
    # now chat says installed: readiness 'ready' + installed => INC-50
    t2 = f"{HIM_DIV} installed"
    persist(
        db,
        android_native.parse(native([("14:00", RAVI, t), ("15:00", RAVI, t2)]), TATA_GROUP),
        TATA_GROUP,
        adapter="t",
    )
    db.flush()
    process_group(
        db,
        g.id,
        ScriptedProvider({t2: cls(t2, status=[("installation", "positive", "i", "installed")])}),
        raise_on_error=True,
    )
    kinds = {r.kind for r in db.scalars(select(m.ReviewItem).where(m.ReviewItem.status == "open"))}
    assert "ready_but_installed" in kinds and "stale_source" not in kinds  # stale conflict retired


def test_maruti_outlet_from_company_channel_town(db: Session) -> None:
    seed_people(db)
    t1 = "Kochi Arena Popular Vehicles: TV installed today"
    t2 = "Popular Vehicles Kochi ARENA TV not working"
    g = load(db, MARUTI_GROUP, [("10:00", ASHA, t1), ("11:00", ASHA, t2)])
    mentions = [("Popular Vehicles", "company"), ("ARENA", "channel"), ("Kochi", "town")]
    prov = ScriptedProvider({
        t1: cls(t1, status=[("installation", "positive", "i", "TV installed")], mentions=mentions),
        t2: cls(t2, issues=[("device_misconfigured", "TV not working", None, "TV not working")], mentions=mentions),
    })  # fmt: skip
    process_group(db, g.id, prov, raise_on_error=True)
    outlets = db.scalars(select(m.Entity).where(m.Entity.type == "outlet")).all()
    assert (
        len(outlets) == 1
        and outlets[0].provisional
        and outlets[0].canonical_key == "popular vehicles|arena|kochi"
    )
    assert count(db, m.Event) == 2 and all(
        e.entity_id == outlets[0].id for e in db.scalars(select(m.Event))
    )
    assert db.scalar(select(m.Client).where(m.Client.id == outlets[0].client_id)).key == "maruti"  # type: ignore[union-attr]
