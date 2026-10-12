"""Persist parsed envelopes: group registry, idempotent merge by logical key, enrichment,
D2 hard-deletes and D3 edits (build brief 6.4, 6.5)."""

from __future__ import annotations

import mimetypes
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.blobstore import BlobStore
from app.core.time import truncate_minute
from app.db.models import (
    Attachment,
    AuditLog,
    Event,
    EventEvidence,
    Group,
    GroupAlias,
    Import,
    MessageVersion,
    Person,
    ReviewItem,
    SourceMessage,
)
from app.ingest.adapters.base import ParseResult
from app.ingest.envelope import MessageEnvelope
from app.ingest.keys import assign_logical_keys
from app.ingest.text import content_fingerprint


@dataclass
class ImportStats:
    created: int = 0
    merged: int = 0
    enriched: int = 0
    deleted: int = 0
    tombstones: int = 0
    edited: int = 0
    quote_links: int = 0
    attachments: int = 0
    missing_files: int = 0
    affected_entity_ids: set[uuid.UUID] = field(default_factory=set)

    def as_json(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "affected_entity_ids"}
        d["affected_entities"] = len(self.affected_entity_ids)
        return d


def get_or_create_group(session: Session, name: str) -> tuple[Group, bool]:
    g = session.scalar(select(Group).where(Group.name == name))
    if g is None:
        alias = session.scalar(select(GroupAlias).where(GroupAlias.alias == name))
        g = session.get(Group, alias.group_id) if alias else None
    if g is not None:
        return g, False
    g = Group(name=name)
    session.add(g)
    session.flush()
    session.add(
        ReviewItem(
            kind="unknown_group_client",
            group_id=g.id,
            subject_refs={"group_id": str(g.id), "group_name": name},
            note="Group auto-registered with no client (D11)",
        )
    )
    return g, True


def _ensure_person(session: Session, env: MessageEnvelope) -> None:
    if not env.sender_key:
        return
    if session.scalar(select(Person).where(Person.sender_key == env.sender_key)) is None:
        src = "confirmed" if env.sender_key.isdigit() else "display_name"
        session.add(
            Person(sender_key=env.sender_key, display_label=env.sender_label or "", source=src)
        )
        session.flush()


def hard_delete_message(
    session: Session, store: BlobStore | None, msg: SourceMessage, stats: ImportStats
) -> None:
    """D2: remove the message and everything supported only by it. Audit stores ids only."""
    mid = msg.id
    att_rows = session.execute(
        select(Attachment.id, Attachment.sha256, Attachment.storage_key).where(
            Attachment.message_id == mid
        )
    ).all()
    event_ids = set(
        session.scalars(select(EventEvidence.event_id).where(EventEvidence.message_id == mid))
    )
    for ev in event_ids:
        e = session.get(Event, ev)
        if e and e.entity_id:
            stats.affected_entity_ids.add(e.entity_id)
    from app.db.models import Observation

    for ent in session.scalars(
        select(Observation.entity_id).where(Observation.source_message_id == mid)
    ):
        if ent:
            stats.affected_entity_ids.add(ent)
    session.add(
        AuditLog(
            actor="system",
            action="source_message_deleted",
            table_name="source_messages",
            row_id=str(mid),
            after={
                "attachment_ids": [str(a.id) for a in att_rows],
                "event_ids": [str(e) for e in event_ids],
            },
            reason="deleted by sender (D2)",
        )
    )
    session.query(SourceMessage).filter(SourceMessage.reply_to_message_id == mid).update(
        {SourceMessage.reply_to_message_id: None}
    )
    tomb = SourceMessage(
        group_id=msg.group_id,
        logical_key=f"deleted|{msg.logical_key}",  # hash-only marker so re-importing the old
        sender_key=msg.sender_key,  # export does not resurrect the message
        sender_label=msg.sender_label,
        sent_at=msg.sent_at,
        sent_at_precision=msg.sent_at_precision,
        message_type="deleted",
        processing_state="IGNORED_FOR_OPERATIONS",
    )
    session.delete(msg)  # cascades: versions, attachments, observations, evidence, claims (DB FKs)
    session.flush()
    session.add(tomb)
    for ev in event_ids:
        remaining = session.scalar(
            select(func.count()).select_from(EventEvidence).where(EventEvidence.event_id == ev)
        )
        if not remaining:
            e = session.get(Event, ev)
            if e:
                session.delete(e)
    session.flush()
    if store:
        for _, sha, key in att_rows:
            if (
                key
                and sha
                and not session.scalar(
                    select(func.count()).select_from(Attachment).where(Attachment.sha256 == sha)
                )
            ):
                store.delete(key)
    stats.deleted += 1


def _link_attachments(
    session: Session,
    msg: SourceMessage,
    env: MessageEnvelope,
    files: Mapping[str, Path],
    store: BlobStore | None,
    stats: ImportStats,
    used: set[str],
) -> None:
    for ref in env.attachments:
        a = Attachment(
            message_id=msg.id,
            availability=ref.availability,
            original_filename=ref.original_filename,
            export_filename=ref.export_filename,
            doc_kind=None,
        )
        if ref.export_filename:
            path = files.get(ref.export_filename)
            if path is None:
                a.availability = "missing_file"
                stats.missing_files += 1
            elif store is not None:
                sha, key, _ = store.put_file(path)
                a.sha256, a.storage_key, a.bytes = sha, key, path.stat().st_size
                a.mime = mimetypes.guess_type(path.name)[0]
                a.availability = "present"
                used.add(ref.export_filename)
        session.add(a)
        stats.attachments += 1


def persist(
    session: Session,
    result: ParseResult,
    group_name: str,
    *,
    adapter: str,
    file_sha256: str | None = None,
    files: Mapping[str, Path] | None = None,
    store: BlobStore | None = None,
) -> tuple[Import, ImportStats]:
    files = files or {}
    stats = ImportStats()
    group, _ = get_or_create_group(session, group_name)
    imp = Import(
        group_id=group.id,
        adapter=adapter,
        file_sha256=file_sha256,
        detected_format=result.format,
        date_order=result.date_order,
        date_order_evidence=result.date_order_evidence,
        warnings=list(result.warnings),
        status="PROCESSING",
    )
    session.add(imp)
    session.flush()
    if result.needs_review:
        imp.status = "NEEDS_REVIEW"
        session.add(
            ReviewItem(
                kind="date_order_ambiguous",
                group_id=group.id,
                subject_refs={"import_id": str(imp.id)},
                candidates=[{"order": "DMY"}, {"order": "MDY"}],
                proposed_value=result.date_order_evidence,
            )
        )
        return imp, stats

    envs = result.envelopes
    assign_logical_keys(group.id, envs)
    quotes: list[tuple[SourceMessage, str]] = []
    used_files: set[str] = set()
    keys_in_import = {e.logical_key for e in envs}

    for env in envs:
        if env.message_type == "system":
            t = (env.normalized_text or "").lower()
            if "created group" in t and group.created_on is None:
                group.created_on = env.sent_at
            if "added you" in t and group.number_added_at is None:
                group.number_added_at = env.sent_at
            continue
        _ensure_person(session, env)
        minute = truncate_minute(env.sent_at)
        if env.deleted:
            _handle_deleted(session, store, group, env, minute, imp, stats)
            continue
        if session.scalar(
            select(SourceMessage.id).where(
                SourceMessage.logical_key == f"deleted|{env.logical_key}"
            )
        ):
            continue  # D2: previously hard-deleted; do not resurrect from an older export
        msg = session.scalar(
            select(SourceMessage).where(SourceMessage.logical_key == env.logical_key)
        )
        if msg is None and env.edited:
            msg = _find_edit_target(session, group, env, minute, keys_in_import)
            if msg is not None:
                session.add(
                    MessageVersion(
                        message_id=msg.id,
                        before_text=msg.raw_text,
                        after_text=env.raw_text,
                        import_id=imp.id,
                    )
                )
                msg.raw_text, msg.normalized_text, msg.edited = (
                    env.raw_text,
                    env.normalized_text,
                    True,
                )
                msg.logical_key = env.logical_key or msg.logical_key
                msg.processing_state = "RECEIVED"  # re-queue extraction for this message only
                stats.edited += 1
        if msg is None:
            msg = SourceMessage(
                group_id=group.id,
                logical_key=env.logical_key,
                sender_key=env.sender_key,
                sender_label=env.sender_label,
                sent_at=env.sent_at,
                sent_at_precision=env.sent_at_precision,
                raw_text=env.raw_text,
                normalized_text=env.normalized_text,
                message_type=env.message_type,
                forwarded=env.forwarded,
                edited=env.edited,
                first_seen_import_id=imp.id,
                seen_in_import_ids=[str(imp.id)],
            )
            session.add(msg)
            session.flush()
            _link_attachments(session, msg, env, files, store, stats, used_files)
            stats.created += 1
        else:
            stats.merged += 1
            enriched = False
            if str(imp.id) not in msg.seen_in_import_ids:
                msg.seen_in_import_ids = [*msg.seen_in_import_ids, str(imp.id)]
            if env.sent_at_precision == "second" and msg.sent_at_precision == "minute":
                msg.sent_at, msg.sent_at_precision, enriched = env.sent_at, "second", True
            if env.forwarded and not msg.forwarded:
                msg.forwarded, enriched = True, True
            if enriched:
                stats.enriched += 1
            if (
                files
                and not session.scalar(
                    select(func.count())
                    .select_from(Attachment)
                    .where(Attachment.message_id == msg.id)
                )
                and env.attachments
            ):
                _link_attachments(session, msg, env, files, store, stats, used_files)
        if env.quoted_context:
            quotes.append((msg, env.quoted_context))
    session.flush()
    for msg, q in quotes:
        target = _resolve_quote(session, group, msg, q)
        if target is not None and msg.reply_to_message_id != target.id:
            msg.reply_to_message_id, msg.reply_method, msg.reply_confidence = (
                target.id,
                "quote",
                1.0,
            )
            stats.quote_links += 1
    unused = sorted(set(files) - used_files)
    if files and unused:
        imp.warnings = [
            *imp.warnings,
            f"{len(unused)} file(s) not referenced by chat: {unused[:10]}",
        ]
    group.last_import_at = func.now()
    imp.stats = stats.as_json()
    imp.status = "PROCESSED"
    session.flush()
    return imp, stats


def _handle_deleted(
    session: Session,
    store: BlobStore | None,
    group: Group,
    env: MessageEnvelope,
    minute: Any,
    imp: Import,
    stats: ImportStats,
) -> None:
    from datetime import timedelta

    existing = session.scalars(
        select(SourceMessage).where(
            SourceMessage.group_id == group.id,
            SourceMessage.sender_key == env.sender_key,
            SourceMessage.message_type != "deleted",
            SourceMessage.sent_at >= minute,
            SourceMessage.sent_at < minute + timedelta(minutes=1),
        )
    ).all()
    if existing:
        # INC-14/15: a real message previously held at this slot is now deleted -> hard-delete (D2)
        hard_delete_message(session, store, existing[0], stats)
        return
    key = env.logical_key
    slot_has_tombstone = session.scalar(
        select(SourceMessage.id).where(
            SourceMessage.group_id == group.id,
            SourceMessage.sender_key == env.sender_key,
            SourceMessage.message_type == "deleted",
            SourceMessage.sent_at >= minute,
            SourceMessage.sent_at < minute + timedelta(minutes=1),
        )
    )
    if (
        not slot_has_tombstone
        and session.scalar(select(SourceMessage.id).where(SourceMessage.logical_key == key)) is None
    ):
        session.add(
            SourceMessage(
                group_id=group.id,
                logical_key=key,
                sender_key=env.sender_key,
                sender_label=env.sender_label,
                sent_at=env.sent_at,
                sent_at_precision=env.sent_at_precision,
                message_type="deleted",
                first_seen_import_id=imp.id,
                seen_in_import_ids=[str(imp.id)],
                processing_state="IGNORED_FOR_OPERATIONS",
            )
        )
        stats.tombstones += 1


def _find_edit_target(
    session: Session, group: Group, env: MessageEnvelope, minute: Any, keys: set[str | None]
) -> SourceMessage | None:
    from datetime import timedelta

    for cand in session.scalars(
        select(SourceMessage).where(
            SourceMessage.group_id == group.id,
            SourceMessage.sender_key == env.sender_key,
            SourceMessage.message_type == env.message_type,
            SourceMessage.sent_at >= minute,
            SourceMessage.sent_at < minute + timedelta(minutes=1),
        )
    ):
        if cand.logical_key not in keys:
            return cand
    return None


def _resolve_quote(
    session: Session, group: Group, msg: SourceMessage, quote: str
) -> SourceMessage | None:
    sender, _, text = quote.partition(": ")
    text = text.strip().rstrip("…").strip()
    if not text:
        return None
    q = select(SourceMessage).where(
        SourceMessage.group_id == group.id,
        SourceMessage.sent_at <= msg.sent_at,
        SourceMessage.id != msg.id,
        SourceMessage.normalized_text.startswith(text[:80]),
    )
    if sender:
        q = q.where(SourceMessage.sender_label == sender)
    return session.scalars(q.order_by(SourceMessage.sent_at.desc())).first()


__all__ = ["ImportStats", "persist", "hard_delete_message", "content_fingerprint"]
