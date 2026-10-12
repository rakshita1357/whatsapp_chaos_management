"""Commit step: turn one validated bundle into events, observations, claims and review items.

Layering: source messages are never touched; observations record what a source said. Re-running a
bundle replaces ONLY that bundle's own outputs for the same extractor version (idempotent), so
reprocessing never duplicates (build brief 7.1)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.time import ist_date
from app.db.models import (
    AggregateClaim,
    Attachment,
    Event,
    EventEvidence,
    Observation,
    SourceMessage,
)
from app.entities.resolve import Resolution
from app.review.items import clear_open_items, raise_review
from app.state.followups import eta_for_message, next_lifecycle_on_ack
from app.understand.classify import ValidatedClassification
from app.understand.reply_links import Decision

EXTRACTOR = "chat-text"

# (dimension, polarity) -> (event type, observation value)
STATUS_MAP: dict[tuple[str, str], tuple[str, str]] = {
    ("installation", "positive"): ("installation_report", "installation_reported"),
    ("installation", "negative"): ("not_installed_report", "not_installed_reported"),
    ("activation", "positive"): ("activation_confirmed", "activated"),
    ("activation", "negative"): ("activation_report", "not_activated"),
    ("readiness", "positive"): ("readiness_change", "ready"),
    ("readiness", "negative"): ("readiness_change", "not_ready"),
}


@dataclass
class BundleInput:
    group_id: uuid.UUID
    messages: list[SourceMessage]
    attachments: list[Attachment]
    classification: ValidatedClassification
    resolution: Resolution
    text: str
    extractor_version: str
    link: Decision | None = None
    link_method: str | None = None  # self_identifying | quote | adjacent | scored
    link_target_entity: uuid.UUID | None = None
    link_target_message: uuid.UUID | None = None
    link_confidence: float | None = None
    assigned_person_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass
class CommitResult:
    event_ids: list[uuid.UUID] = field(default_factory=list)
    observation_count: int = 0
    claim_count: int = 0
    needs_review: bool = False
    affected_entities: set[uuid.UUID] = field(default_factory=set)
    entity_id: uuid.UUID | None = None


def clear_outputs(session: Session, message_ids: list[uuid.UUID], version: str) -> set[uuid.UUID]:
    """Delete this extractor version's previous outputs for these messages. Returns entities touched
    (their state must be re-derived)."""
    touched: set[uuid.UUID] = set()
    ev_ids = list(
        session.scalars(
            select(EventEvidence.event_id)
            .join(Event, Event.id == EventEvidence.event_id)
            .where(
                EventEvidence.message_id.in_(message_ids),
                EventEvidence.role == "primary",
                Event.extractor_version == version,
            )
        )
    )
    if ev_ids:
        touched |= {
            e for e in session.scalars(select(Event.entity_id).where(Event.id.in_(ev_ids))) if e
        }
    touched |= {
        e
        for e in session.scalars(
            select(Observation.entity_id).where(
                Observation.source_message_id.in_(message_ids),
                Observation.extractor_version == version,
            )
        )
        if e
    }
    session.execute(
        delete(Observation).where(
            Observation.source_message_id.in_(message_ids),
            Observation.extractor_version == version,
            Observation.extractor == EXTRACTOR,
        )
    )
    if ev_ids:
        session.execute(delete(Event).where(Event.id.in_(ev_ids)))
    session.execute(
        delete(EventEvidence).where(
            EventEvidence.message_id.in_(message_ids), EventEvidence.role == "ack"
        )
    )
    session.execute(delete(AggregateClaim).where(AggregateClaim.message_id.in_(message_ids)))
    for mid in message_ids:
        clear_open_items(session, f"msg:{mid}:")
    return touched


def _evidence(session: Session, ev: Event, b: BundleInput) -> None:
    for i, m in enumerate(b.messages):
        session.add(
            EventEvidence(
                id=uuid.uuid4(),
                event_id=ev.id,
                message_id=m.id,
                role="primary" if i == 0 else "bundle",
            )
        )
    for a in b.attachments:
        session.add(
            EventEvidence(id=uuid.uuid4(), event_id=ev.id, attachment_id=a.id, role="attachment")
        )


def commit_bundle(session: Session, b: BundleInput) -> CommitResult:
    res = CommitResult()
    primary = b.messages[0]
    c = b.classification.c
    ids = [m.id for m in b.messages]
    res.affected_entities |= clear_outputs(session, ids, b.extractor_version)
    sent, eff = primary.sent_at, ist_date(primary.sent_at)

    # who is this about? a self-identifying code beats any inferred thread link
    entity_id = b.resolution.entity_id
    link_method = "self_identifying" if entity_id else None
    link_conf = b.resolution.confidence if entity_id else None
    if entity_id is None and b.link_method and b.link_target_entity:
        entity_id, link_method, link_conf = b.link_target_entity, b.link_method, b.link_confidence
    res.entity_id = entity_id
    if entity_id:
        res.affected_entities.add(entity_id)

    produces_events = False
    creates_status = b.classification.creates_status_events

    def new_event(type_: str, payload: dict[str, Any], **kw: Any) -> Event:
        ev = Event(
            id=uuid.uuid4(),
            type=type_,
            job_types=list(c.job_types),
            entity_id=entity_id,
            group_id=b.group_id,
            opened_at=sent,
            link_method=link_method,
            link_confidence=link_conf,
            extractor_version=b.extractor_version,
            payload=payload,
            **kw,
        )
        session.add(ev)
        session.flush()
        _evidence(session, ev, b)
        res.event_ids.append(ev.id)
        return ev

    def new_obs(
        dimension: str, value: str, polarity: str, span: list[int], ev: Event, raw_value: str
    ) -> None:
        session.add(
            Observation(
                id=uuid.uuid4(),
                entity_id=entity_id,
                event_id=ev.id,
                dimension=dimension,
                value=value,
                polarity=polarity,
                observed_at=sent,
                effective_date=eff,
                source_kind="message",
                source_message_id=primary.id,
                source_ref={"message_id": str(primary.id), "span": span, "model_value": raw_value},
                extractor=EXTRACTOR,
                extractor_version=b.extractor_version,
                confidence=c.confidence,
            )
        )
        res.observation_count += 1

    if c.intent == "ACKNOWLEDGEMENT":
        if entity_id:
            _apply_ack(session, entity_id, b, primary)
    else:
        if creates_status:
            for s in c.status_assertions:
                etype, value = STATUS_MAP[(s.dimension, s.polarity)]
                # a mention on a "not done" report is an assignment ("NO TV INSTALLED @Ravi check");
                # on a positive report it is usually thanks, so it assigns nobody
                assignee = (
                    b.assigned_person_ids[0]
                    if b.assigned_person_ids and s.polarity == "negative"
                    else None
                )
                ev = new_event(
                    etype,
                    {"dimension": s.dimension, "model_value": s.value, "span": s.span},
                    assigned_to_person_id=assignee,
                )
                new_obs(s.dimension, value, s.polarity, s.span, ev, s.value)
                produces_events = True
            for i in c.issue_assertions:
                window = eta_for_message(i.eta_text, sent)
                ev = new_event(
                    "issue",
                    {
                        "issue_type": i.type,
                        "detail": i.detail,
                        "eta_text": i.eta_text,
                        "span": i.span,
                    },
                    lifecycle_status="open",
                    follow_up_due_from=window[0] if window else None,
                    follow_up_due_to=window[1] if window else None,
                    assigned_to_person_id=b.assigned_person_ids[0]
                    if b.assigned_person_ids
                    else None,
                )
                new_obs("issue", i.type, "negative", i.span, ev, i.type)
                produces_events = True
            if c.intent == "UPDATE_REQUEST" and not produces_events:
                etype = "activation_request" if "activation" in c.job_types else "update_request"
                new_event(
                    etype,
                    {"job_types": c.job_types},
                    assigned_to_person_id=b.assigned_person_ids[0]
                    if b.assigned_person_ids
                    else None,
                )
                produces_events = True
            if (
                not produces_events
                and b.attachments
                and c.intent in ("ATTACHMENT_WITH_CONTEXT", "DOCUMENT_UPLOAD")
                and entity_id
            ):
                new_event("document_upload", {})
                produces_events = True
        for claim in c.aggregate_claims:  # group-level counts: claims, never events
            if claim.scope == "yesterday":
                scope_date, partial = eff - timedelta(days=1), None
            elif claim.scope == "today":
                scope_date, partial = eff, sent  # the day is not over: partial as of this message
            else:
                scope_date, partial = None, None
            session.add(
                AggregateClaim(
                    id=uuid.uuid4(),
                    message_id=primary.id,
                    metric=claim.metric,
                    scope_date=scope_date,
                    scope_partial_as_of=partial,
                    value=claim.value,
                    value_qualifier=claim.qualifier,
                    extra={"scope": claim.scope, "note": claim.note},
                )
            )
            res.claim_count += 1

    # review items for anything uncertain: never guess
    refs = {"message_id": str(primary.id), "event_ids": [str(e) for e in res.event_ids]}
    if b.resolution.ambiguity:
        raise_review(
            session,
            b.resolution.ambiguity,
            dedupe_key=f"msg:{primary.id}:{b.resolution.ambiguity}",
            group_id=b.group_id,
            subject_refs=refs,
            candidates=b.resolution.candidates,
            note="Message names a site but it could not be resolved to exactly one",
        )
        res.needs_review = True
    if produces_events and entity_id is None and b.link is not None and b.link.kind == "review":
        raise_review(
            session,
            "reply_link_ambiguous",
            dedupe_key=f"msg:{primary.id}:reply_link_ambiguous",
            group_id=b.group_id,
            subject_refs=refs,
            candidates=[x.as_json() for x in b.link.ranked[:5]],
            note="Which site is this message about? Candidates are ranked best first.",
        )
        res.needs_review = True
    elif produces_events and entity_id is None and not b.resolution.ambiguity:
        raise_review(
            session,
            "entity_match_ambiguous",
            dedupe_key=f"msg:{primary.id}:no_entity",
            group_id=b.group_id,
            subject_refs=refs,
            note="Status reported but no site could be identified",
        )
        res.needs_review = True
    if b.classification.low_confidence and (produces_events or c.aggregate_claims):
        raise_review(
            session,
            "low_confidence_extraction",
            dedupe_key=f"msg:{primary.id}:low_confidence",
            group_id=b.group_id,
            subject_refs=refs,
            proposed_value={"confidence": c.confidence, "intent": c.intent},
            note="The model was not confident about this message",
        )
        res.needs_review = True

    for m in b.messages:
        m.intent = c.intent
        m.processing_state = "NEEDS_REVIEW" if res.needs_review else "PROCESSED"
    if b.link_target_message and link_method in ("quote", "adjacent", "scored") and entity_id:
        primary.reply_to_message_id = b.link_target_message
        primary.reply_method = "quote" if link_method == "quote" else "inferred"
        primary.reply_confidence = link_conf
    session.flush()
    return res


def _apply_ack(
    session: Session, entity_id: uuid.UUID, b: BundleInput, primary: SourceMessage
) -> None:
    """An acknowledgement records that someone saw the thread and moves open -> acknowledged. It
    never resolves anything (INC-51)."""
    events = list(
        session.scalars(
            select(Event).where(Event.entity_id == entity_id).order_by(Event.opened_at.desc())
        )
    )
    if not events:
        return
    session.add(
        EventEvidence(id=uuid.uuid4(), event_id=events[0].id, message_id=primary.id, role="ack")
    )
    for ev in events:
        if ev.type == "issue":
            ev.lifecycle_status = next_lifecycle_on_ack(ev.lifecycle_status)
