"""Per-group understanding pipeline (build brief 7.1):
bundle -> rules -> model -> validate -> resolve entity -> link thread -> commit -> derive state.

Rules go first: acknowledgements and bare pointers never reach the model. Every model output is a
proposal that `validate_classification` constrains. Re-running replaces a bundle's own outputs."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import to_ist
from app.db.models import (
    Attachment,
    Client,
    Entity,
    EntityIdentifier,
    Event,
    EventEvidence,
    Group,
    Person,
    SourceMessage,
)
from app.entities.people import resolve_persons
from app.entities.resolve import NONE, Resolution, resolve_maruti, resolve_tata
from app.providers.base import ModelProvider, ProviderError, Usage
from app.state.conflicts import sync_conflicts
from app.state.derive import derive_entities
from app.state.observations import BundleInput, commit_bundle
from app.understand.bundles import MsgLike, build_bundles
from app.understand.classify import (
    Classification,
    ClassifyContext,
    ValidatedClassification,
    classify,
)
from app.understand.reply_links import (
    Decision,
    LinkInput,
    LinkWeights,
    Thread,
    link_message,
    load_weights,
)
from app.understand.rules import RuleEngine, RuleHints
from app.understand.settings import UnderstandingConfig, load_understanding_config

log = logging.getLogger(__name__)
PROCESSABLE = ("text", "media", "album_header")
ADJACENT_ACK_MINUTES = 10


@dataclass
class ProcessStats:
    bundles: int = 0
    skipped: int = 0
    model_calls: int = 0
    rule_only: int = 0
    failed: int = 0
    events: int = 0
    observations: int = 0
    claims: int = 0
    needs_review: int = 0
    auto_links: int = 0
    review_links: int = 0
    usage: Usage = field(default_factory=Usage)
    affected_entities: set[uuid.UUID] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)


def _state_of(ev: Event) -> str:
    if ev.type == "installation_report":
        return "installed"
    if ev.type == "not_installed_report":
        return "not_installed"
    if ev.type == "issue":
        return (
            "no_screen" if ev.payload.get("issue_type") == "no_screen_available" else "issue_open"
        )
    return "unknown"


def build_open_threads(
    session: Session, group_id: uuid.UUID, at: object, weights: LinkWeights
) -> list[Thread]:
    """Threads = sites with recent chat events in this group (previous N hours)."""
    since = at - timedelta(hours=weights.window_hours)  # type: ignore[operator]
    events = list(
        session.scalars(
            select(Event)
            .where(
                Event.group_id == group_id,
                Event.entity_id.is_not(None),
                Event.opened_at >= since,
                Event.opened_at <= at,
            )
            .order_by(Event.opened_at, Event.id)
        )
    )
    by_entity: dict[uuid.UUID, list[Event]] = {}
    for e in events:
        if e.entity_id:
            by_entity.setdefault(e.entity_id, []).append(e)
    if not by_entity:
        return []
    ev_ids = [e.id for e in events]
    ev_entity = {e.id: e.entity_id for e in events}
    msg_rows = session.execute(
        select(
            EventEvidence.event_id,
            EventEvidence.role,
            SourceMessage.sender_key,
            SourceMessage.sent_at,
            SourceMessage.id,
        )
        .join(SourceMessage, SourceMessage.id == EventEvidence.message_id)
        .where(EventEvidence.event_id.in_(ev_ids), EventEvidence.role.in_(["primary", "ack"]))
    ).all()
    ents = {e.id: e for e in session.scalars(select(Entity).where(Entity.id.in_(list(by_entity))))}
    parents = {
        e.parent_id: e.canonical_key
        for e in session.scalars(
            select(Entity).where(Entity.id.in_([x.parent_id for x in ents.values() if x.parent_id]))
        )
    }
    parent_keys = {e.id: parents.get(e.parent_id) for e in ents.values()}
    idents: dict[uuid.UUID, set[str]] = {}
    for eid, norm in session.execute(
        select(EntityIdentifier.entity_id, EntityIdentifier.normalized_value).where(
            EntityIdentifier.entity_id.in_(list(by_entity))
        )
    ).all():
        idents.setdefault(eid, set()).add(norm)
    people = {p.id: p.sender_key for p in session.scalars(select(Person))}
    threads: list[Thread] = []
    for eid, evs in by_entity.items():
        ent = ents[eid]
        t = Thread(
            key=str(eid),
            label=ent.display_name or ent.canonical_key,
            last_state=_state_of(evs[-1]),
            last_activity=max(e.opened_at for e in evs if e.opened_at),
            entity_id=eid,
        )
        for e in evs:
            if e.assigned_to_person_id and people.get(e.assigned_to_person_id):
                t.assigned_to.add(str(people[e.assigned_to_person_id]))
        newest: tuple[object, uuid.UUID] | None = None
        for ev_id, role, sender_key, sent_at, msg_id in msg_rows:
            if ev_entity.get(ev_id) != eid:
                continue
            if newest is None or sent_at >= newest[0]:  # type: ignore[operator]
                newest = (sent_at, msg_id)
            if not sender_key:
                continue
            if role == "ack":
                t.acked_by.add(sender_key)
                t.last_activity = max(t.last_activity, sent_at)
            else:
                t.substantive_by.add(sender_key)
        t.last_message_id = newest[1] if newest else None
        attrs = ent.attributes or {}
        t.identifiers = {x for x in idents.get(eid, set()) if x} | {
            str(v).casefold() for v in (attrs.get("city"), attrs.get("pin"), attrs.get("town")) if v
        }
        t.identifiers.add(ent.canonical_key)
        if parent_keys.get(eid):
            t.identifiers.add(str(parent_keys[eid]).casefold())
        threads.append(t)
    return threads


class _EntityOfMessage:
    """Which site a message already belongs to (this run's commits first, then the database)."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.cache: dict[uuid.UUID, uuid.UUID | None] = {}

    def get(self, message_id: uuid.UUID) -> uuid.UUID | None:
        if message_id in self.cache:
            return self.cache[message_id]
        e = self.session.scalar(
            select(Event.entity_id)
            .join(EventEvidence, EventEvidence.event_id == Event.id)
            .where(EventEvidence.message_id == message_id, Event.entity_id.is_not(None))
            .limit(1)
        )
        self.cache[message_id] = e
        return e


def _mentions(c: Classification, kind: str) -> list[str]:
    return [m.raw for m in c.entity_mentions if m.kind == kind]


def _rule_only(hints: RuleHints, text: str) -> ValidatedClassification | None:
    """Acknowledgements and bare pointers are decided by rules; no model call (rules first)."""
    from app.understand.rules import POINTER_RE

    if hints.is_ack:
        return ValidatedClassification(
            Classification(intent="ACKNOWLEDGEMENT", confidence=1.0), ["rules: acknowledgement"]
        )
    if hints.is_pointer and not POINTER_RE.sub("", text).strip():
        return ValidatedClassification(
            Classification(intent="POINTER", confidence=1.0), ["rules: bare pointer"]
        )
    return None


def process_group(
    session: Session,
    group_id: uuid.UUID,
    provider: ModelProvider,
    *,
    force: bool = False,
    raise_on_error: bool = False,
    cfg: UnderstandingConfig | None = None,
    weights: LinkWeights | None = None,
    engine: RuleEngine | None = None,
) -> ProcessStats:
    cfg = cfg or load_understanding_config()
    weights = weights or load_weights()
    engine = engine or RuleEngine()
    stats = ProcessStats()
    group = session.get(Group, group_id)
    if group is None:
        raise ValueError("unknown group")
    client = session.get(Client, group.client_id) if group.client_id else None
    client_key = client.key if client else None
    client_id = client.id if client else None

    msgs = list(
        session.scalars(
            select(SourceMessage)
            .where(SourceMessage.group_id == group_id, SourceMessage.message_type.in_(PROCESSABLE))
            .order_by(
                SourceMessage.sent_at,
                SourceMessage.source_line.nulls_last(),
                SourceMessage.created_at,
            )
        )
    )
    atts: dict[uuid.UUID, list[Attachment]] = {}
    if msgs:
        for a in session.scalars(
            select(Attachment).where(Attachment.message_id.in_([m.id for m in msgs]))
        ):
            if a.message_id:
                atts.setdefault(a.message_id, []).append(a)
    like = [
        MsgLike(
            m.id,
            m.sender_key,
            m.sent_at,
            bool(atts.get(m.id)) or m.message_type == "media",
            m.normalized_text or "",
            m.message_type == "album_header",
        )
        for m in msgs
    ]
    groups = build_bundles(like, cfg.bundle_window_seconds, cfg.bundle_short_text_chars)
    where = _EntityOfMessage(session)

    for idxs in groups:
        members = [msgs[i] for i in idxs]
        if not force and all(m.processing_state in ("PROCESSED", "NEEDS_REVIEW") for m in members):
            stats.skipped += 1
            continue
        primary = members[0]
        text = "\n".join(m.normalized_text for m in members if m.normalized_text)
        raw = "\n".join(m.raw_text for m in members if m.raw_text)
        bundle_atts = [a for m in members for a in atts.get(m.id, [])]
        hints = engine.analyse(text, raw)
        try:
            v = _rule_only(hints, text)
            if v is None:
                prev = [
                    {
                        "sender": msgs[i].sender_label or "",
                        "time": to_ist(msgs[i].sent_at).strftime("%H:%M"),
                        "text": (msgs[i].normalized_text or "")[:200],
                    }
                    for i in range(max(0, idxs[0] - cfg.context_messages), idxs[0])
                ]
                ctx = ClassifyContext(
                    sender=primary.sender_label,
                    sent_at_ist=to_ist(primary.sent_at).strftime("%Y-%m-%d %H:%M"),
                    text=text,
                    attachment_kinds=sorted(
                        {a.doc_kind or (a.mime or "file").split("/")[0] for a in bundle_atts}
                    ),
                    previous=prev,
                    quoted=None,
                )
                v = classify(provider, ctx, hints, cfg)
                stats.model_calls += 1
                stats.usage = Usage(
                    stats.usage.input_tokens + v.usage.input_tokens,
                    stats.usage.output_tokens + v.usage.output_tokens,
                    stats.usage.cache_read_tokens + v.usage.cache_read_tokens,
                )
            else:
                stats.rule_only += 1
        except ProviderError as exc:
            if raise_on_error:
                raise
            for m in members:
                m.processing_state = "FAILED_RETRYABLE"
            stats.failed += 1
            stats.errors.append(f"{type(exc).__name__}: {str(exc)[:160]}")
            continue

        c = v.c
        # ---- who is it about?
        resolution = _resolve(session, client_key, client_id, c, hints)
        link: Decision | None = None
        link_method: str | None = None
        link_entity: uuid.UUID | None = None
        link_msg: uuid.UUID | None = None
        link_conf: float | None = None
        prev_msg = msgs[idxs[0] - 1] if idxs[0] > 0 else None
        if resolution.entity_id is None and not resolution.ambiguity:
            if primary.reply_method == "quote" and primary.reply_to_message_id:
                link_entity = where.get(primary.reply_to_message_id)
                if link_entity:
                    link_method, link_msg, link_conf = "quote", primary.reply_to_message_id, 1.0
            elif c.intent in ("ACKNOWLEDGEMENT", "POINTER") and prev_msg is not None:
                gap = (primary.sent_at - prev_msg.sent_at).total_seconds() / 60
                other_sender = prev_msg.sender_key != primary.sender_key
                if gap <= ADJACENT_ACK_MINUTES and (other_sender or c.intent == "POINTER"):
                    link_entity = where.get(prev_msg.id)
                    if link_entity:
                        link_method, link_msg = "adjacent", prev_msg.id
                        link_conf = 0.95 if c.intent == "POINTER" else 0.7
            elif c.status_assertions or c.issue_assertions:
                first = c.status_assertions[0] if c.status_assertions else None
                threads = build_open_threads(session, group_id, primary.sent_at, weights)
                link = link_message(
                    LinkInput(
                        primary.sender_key,
                        primary.sent_at,
                        "issue" if c.issue_assertions else "status",
                        first.dimension if first else None,
                        first.polarity if first else None,
                    ),
                    threads,
                    weights,
                )
                if link.kind == "auto" and link.top and link.top.entity_id:
                    link_entity, link_method, link_conf = (
                        link.top.entity_id,
                        "scored",
                        link.top.score,
                    )
                    link_msg = next(
                        (t.last_message_id for t in threads if t.key == link.top.key), None
                    )
                    stats.auto_links += 1
                elif link.kind == "review":
                    stats.review_links += 1
        mention_pairs = list(
            zip([h.value for h in hints.mentions], hints.mention_kinds, strict=True)
        )
        persons = resolve_persons(session, mention_pairs)
        res = commit_bundle(
            session,
            BundleInput(
                group_id=group_id,
                messages=members,
                attachments=bundle_atts,
                classification=v,
                resolution=resolution,
                text=text,
                extractor_version=cfg.extractor_version,
                link=link,
                link_method=link_method,
                link_target_entity=link_entity,
                link_target_message=link_msg,
                link_confidence=link_conf,
                assigned_person_ids=[p.id for p in persons],
            ),
        )
        for m in members:
            where.cache[m.id] = res.entity_id
        stats.bundles += 1
        stats.events += len(res.event_ids)
        stats.observations += res.observation_count
        stats.claims += res.claim_count
        stats.needs_review += int(res.needs_review)
        stats.affected_entities |= res.affected_entities

    derive_entities(session, stats.affected_entities)
    for eid in stats.affected_entities:
        sync_conflicts(session, eid)
    session.flush()
    return stats


def _resolve(
    session: Session,
    client_key: str | None,
    client_id: uuid.UUID | None,
    c: Classification,
    hints: RuleHints,
) -> Resolution:
    if client_key == "maruti":
        companies, channels, towns = (
            _mentions(c, "company"),
            _mentions(c, "channel"),
            _mentions(c, "town"),
        )
        return resolve_maruti(
            session,
            client_id,
            companies[0] if companies else None,
            channels[0] if channels else None,
            towns[0] if towns else None,
        )
    divisions = [h.value for h in hints.division_codes] + _mentions(c, "division_code")
    dealers = [h.value for h in hints.dealer_codes] + _mentions(c, "dealer_code")
    if not divisions and not dealers:
        return NONE
    return resolve_tata(session, client_id, divisions, dealers, _mentions(c, "town"))
