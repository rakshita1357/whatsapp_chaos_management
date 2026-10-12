"""Schema. Layers kept apart: source (messages/attachments/workbook rows) ->
observations (append-only) -> derived state (entity_state)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _fk(target: str, nullable: bool = False, ondelete: str | None = None) -> Mapped[Any]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), nullable=nullable
    )


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _ts(nullable: bool = True) -> Mapped[datetime | None]:
    return mapped_column(DateTime(timezone=True), nullable=nullable)


# ---------------------------------------------------------------- tenancy / people
class Client(Base):
    __tablename__ = "clients"
    id: Mapped[uuid.UUID] = _pk()
    key: Mapped[str] = mapped_column(String(32), unique=True)  # tata, maruti
    name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = _created()


class Group(Base):
    __tablename__ = "groups"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(256), unique=True)
    client_id: Mapped[uuid.UUID | None] = _fk("clients.id", nullable=True)
    created_on: Mapped[datetime | None] = _ts()
    number_added_at: Mapped[datetime | None] = _ts()
    last_import_at: Mapped[datetime | None] = _ts()
    visibility_state: Mapped[str] = mapped_column(String(32), default="visible")
    created_at: Mapped[datetime] = _created()


class GroupAlias(Base):
    __tablename__ = "group_aliases"
    id: Mapped[uuid.UUID] = _pk()
    group_id: Mapped[uuid.UUID] = _fk("groups.id", ondelete="CASCADE")
    alias: Mapped[str] = mapped_column(String(256), unique=True)
    created_at: Mapped[datetime] = _created()


class Person(Base):
    __tablename__ = "people"
    id: Mapped[uuid.UUID] = _pk()
    sender_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    display_label: Mapped[str] = mapped_column(String(256))
    name: Mapped[str | None] = mapped_column(String(256))
    team: Mapped[str | None] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(16), default="display_name")
    created_at: Mapped[datetime] = _created()


class PersonAlias(Base):
    __tablename__ = "person_aliases"
    id: Mapped[uuid.UUID] = _pk()
    person_id: Mapped[uuid.UUID] = _fk("people.id", ondelete="CASCADE")
    alias: Mapped[str] = mapped_column(String(256), index=True)
    created_at: Mapped[datetime] = _created()


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk()
    username: Mapped[str] = mapped_column(String(64), unique=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created()


class UserGroupAccess(Base):
    __tablename__ = "user_group_access"
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )


# ---------------------------------------------------------------- source layer
class Import(Base):
    __tablename__ = "imports"
    id: Mapped[uuid.UUID] = _pk()
    group_id: Mapped[uuid.UUID | None] = _fk("groups.id", nullable=True)
    adapter: Mapped[str] = mapped_column(String(64))
    file_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    detected_format: Mapped[str | None] = mapped_column(String(32))
    date_order: Mapped[str | None] = mapped_column(String(8))  # DMY / MDY
    date_order_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="RECEIVED")
    warnings: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = _created()


class SourceMessage(Base):
    __tablename__ = "source_messages"
    id: Mapped[uuid.UUID] = _pk()
    group_id: Mapped[uuid.UUID] = _fk("groups.id")
    logical_key: Mapped[str] = mapped_column(String(512), unique=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(256))
    sender_key: Mapped[str | None] = mapped_column(String(128))
    sender_label: Mapped[str | None] = mapped_column(String(256))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at_precision: Mapped[str] = mapped_column(String(8), default="minute")
    source_line: Mapped[int | None] = mapped_column(Integer)  # order within a minute (file order)
    raw_text: Mapped[str | None] = mapped_column(Text)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    message_type: Mapped[str] = mapped_column(String(16), default="text")
    forwarded: Mapped[bool | None] = mapped_column(Boolean)  # NULL = unknown
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    reply_to_message_id: Mapped[uuid.UUID | None] = _fk(
        "source_messages.id", nullable=True, ondelete="SET NULL"
    )
    reply_method: Mapped[str | None] = mapped_column(String(16))
    reply_confidence: Mapped[float | None] = mapped_column(Float)
    intent: Mapped[str | None] = mapped_column(String(48))
    processing_state: Mapped[str] = mapped_column(String(32), default="RECEIVED")
    first_seen_import_id: Mapped[uuid.UUID | None] = _fk("imports.id", nullable=True)
    seen_in_import_ids: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("ix_msg_group_sent", "group_id", "sent_at"),)


class MessageVersion(Base):
    __tablename__ = "message_versions"
    id: Mapped[uuid.UUID] = _pk()
    message_id: Mapped[uuid.UUID] = _fk("source_messages.id", ondelete="CASCADE")
    before_text: Mapped[str | None] = mapped_column(Text)
    after_text: Mapped[str | None] = mapped_column(Text)
    import_id: Mapped[uuid.UUID | None] = _fk("imports.id", nullable=True)
    created_at: Mapped[datetime] = _created()


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[uuid.UUID] = _pk()
    message_id: Mapped[uuid.UUID | None] = _fk(
        "source_messages.id", nullable=True, ondelete="CASCADE"
    )
    availability: Mapped[str] = mapped_column(String(24), default="present")
    original_filename: Mapped[str | None] = mapped_column(String(512))
    export_filename: Mapped[str | None] = mapped_column(String(512))
    mime: Mapped[str | None] = mapped_column(String(128))
    bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    phash: Mapped[str | None] = mapped_column(String(32))
    storage_key: Mapped[str | None] = mapped_column(String(256))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_s: Mapped[float | None] = mapped_column(Float)
    doc_kind: Mapped[str | None] = mapped_column(String(32))
    parse_state: Mapped[str] = mapped_column(String(32), default="RECEIVED")
    created_at: Mapped[datetime] = _created()


class AttachmentSimilarity(Base):
    __tablename__ = "attachment_similarity"
    id: Mapped[uuid.UUID] = _pk()
    a_id: Mapped[uuid.UUID] = _fk("attachments.id", ondelete="CASCADE")
    b_id: Mapped[uuid.UUID] = _fk("attachments.id", ondelete="CASCADE")
    distance: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(16))
    verdict: Mapped[str] = mapped_column(String(24), default="suggested")
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("a_id", "b_id"),)


# ---------------------------------------------------------------- entities
class Entity(Base):
    __tablename__ = "entities"
    id: Mapped[uuid.UUID] = _pk()
    type: Mapped[str] = mapped_column(String(24))
    client_id: Mapped[uuid.UUID | None] = _fk("clients.id", nullable=True)
    canonical_key: Mapped[str] = mapped_column(String(512))
    display_name: Mapped[str | None] = mapped_column(String(512))
    parent_id: Mapped[uuid.UUID | None] = _fk("entities.id", nullable=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    provisional: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (UniqueConstraint("type", "client_id", "canonical_key"),)


class EntityIdentifier(Base):
    __tablename__ = "entity_identifiers"
    id: Mapped[uuid.UUID] = _pk()
    entity_id: Mapped[uuid.UUID] = _fk("entities.id", ondelete="CASCADE")
    kind: Mapped[str] = mapped_column(String(24))
    raw_value: Mapped[str] = mapped_column(String(512))
    normalized_value: Mapped[str] = mapped_column(String(512), index=True)
    source: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()


# ---------------------------------------------------------------- events / observations
class Event(Base):
    __tablename__ = "events"
    id: Mapped[uuid.UUID] = _pk()
    type: Mapped[str] = mapped_column(String(40))
    job_types: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    entity_id: Mapped[uuid.UUID | None] = _fk("entities.id", nullable=True)
    group_id: Mapped[uuid.UUID | None] = _fk("groups.id", nullable=True)
    opened_at: Mapped[datetime | None] = _ts()
    closed_at: Mapped[datetime | None] = _ts()
    lifecycle_status: Mapped[str | None] = mapped_column(String(24))
    assigned_to_person_id: Mapped[uuid.UUID | None] = _fk("people.id", nullable=True)
    follow_up_due_from: Mapped[date | None] = mapped_column(Date)
    follow_up_due_to: Mapped[date | None] = mapped_column(Date)
    link_method: Mapped[str | None] = mapped_column(String(24))
    link_confidence: Mapped[float | None] = mapped_column(Float)
    extractor_version: Mapped[str | None] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = _created()


class EventEvidence(Base):
    __tablename__ = "event_evidence"
    id: Mapped[uuid.UUID] = _pk()
    event_id: Mapped[uuid.UUID] = _fk("events.id", ondelete="CASCADE")
    message_id: Mapped[uuid.UUID | None] = _fk(
        "source_messages.id", nullable=True, ondelete="CASCADE"
    )
    attachment_id: Mapped[uuid.UUID | None] = _fk(
        "attachments.id", nullable=True, ondelete="CASCADE"
    )
    workbook_row_id: Mapped[uuid.UUID | None] = _fk(
        "workbook_rows.id", nullable=True, ondelete="CASCADE"
    )
    role: Mapped[str] = mapped_column(String(32), default="primary")
    created_at: Mapped[datetime] = _created()


class Observation(Base):
    """Append-only. Corrections add a new row with `supersedes`; nothing is updated in place."""

    __tablename__ = "observations"
    id: Mapped[uuid.UUID] = _pk()
    entity_id: Mapped[uuid.UUID | None] = _fk("entities.id", nullable=True)
    event_id: Mapped[uuid.UUID | None] = _fk("events.id", nullable=True, ondelete="SET NULL")
    dimension: Mapped[str] = mapped_column(String(32))
    value: Mapped[str | None] = mapped_column(Text)
    polarity: Mapped[str | None] = mapped_column(String(8))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_date: Mapped[date | None] = mapped_column(Date)
    source_kind: Mapped[str] = mapped_column(String(16))
    source_message_id: Mapped[uuid.UUID | None] = _fk(
        "source_messages.id", nullable=True, ondelete="CASCADE"
    )
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    extractor: Mapped[str] = mapped_column(String(64))
    extractor_version: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float | None] = mapped_column(Float)
    review_status: Mapped[str] = mapped_column(String(16), default="unreviewed")
    supersedes: Mapped[uuid.UUID | None] = _fk("observations.id", nullable=True)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("ix_obs_entity_dim", "entity_id", "dimension", "observed_at"),)


class AggregateClaim(Base):
    """Group-level counts ("yesterday 15"). Deliberately no FK to events."""

    __tablename__ = "aggregate_claims"
    id: Mapped[uuid.UUID] = _pk()
    message_id: Mapped[uuid.UUID] = _fk("source_messages.id", ondelete="CASCADE")
    metric: Mapped[str] = mapped_column(String(64))
    scope_date: Mapped[date | None] = mapped_column(Date)
    scope_partial_as_of: Mapped[datetime | None] = _ts()
    value: Mapped[int] = mapped_column(Integer)
    value_qualifier: Mapped[str] = mapped_column(String(2), default="=")
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created()


class EntityState(Base):
    __tablename__ = "entity_state"
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True
    )
    dimension: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    as_of: Mapped[datetime | None] = _ts()
    derived_from_observation_ids: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    conflict_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ---------------------------------------------------------------- workbooks
class WorkbookImport(Base):
    __tablename__ = "workbook_imports"
    id: Mapped[uuid.UUID] = _pk()
    attachment_id: Mapped[uuid.UUID | None] = _fk("attachments.id", nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    family_key: Mapped[str] = mapped_column(String(512), index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    role: Mapped[str] = mapped_column(String(24), default="UNKNOWN")
    posted_at: Mapped[datetime | None] = _ts()
    posted_by: Mapped[str | None] = mapped_column(String(256))
    opened_via: Mapped[str] = mapped_column(String(12), default="plain")
    warnings: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = _created()


class WorkbookSheet(Base):
    __tablename__ = "workbook_sheets"
    id: Mapped[uuid.UUID] = _pk()
    import_id: Mapped[uuid.UUID] = _fk("workbook_imports.id", ondelete="CASCADE")
    name: Mapped[str] = mapped_column(String(256))
    visible: Mapped[bool] = mapped_column(Boolean, default=True)
    kind: Mapped[str] = mapped_column(String(24))
    header_row: Mapped[int | None] = mapped_column(Integer)
    max_row: Mapped[int | None] = mapped_column(Integer)
    max_col_effective: Mapped[int | None] = mapped_column(Integer)
    column_mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created()


class WorkbookRow(Base):
    __tablename__ = "workbook_rows"
    id: Mapped[uuid.UUID] = _pk()
    sheet_id: Mapped[uuid.UUID] = _fk("workbook_sheets.id", ondelete="CASCADE")
    row_number: Mapped[int] = mapped_column(Integer)
    row_key: Mapped[str | None] = mapped_column(String(512), index=True)
    entity_id: Mapped[uuid.UUID | None] = _fk("entities.id", nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    normalized: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    cell_types: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    issues: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = _created()


class WorkbookDiff(Base):
    __tablename__ = "workbook_diffs"
    id: Mapped[uuid.UUID] = _pk()
    from_import_id: Mapped[uuid.UUID] = _fk("workbook_imports.id", ondelete="CASCADE")
    to_import_id: Mapped[uuid.UUID] = _fk("workbook_imports.id", ondelete="CASCADE")
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created()


class WorkbookDiffItem(Base):
    __tablename__ = "workbook_diff_items"
    id: Mapped[uuid.UUID] = _pk()
    diff_id: Mapped[uuid.UUID] = _fk("workbook_diffs.id", ondelete="CASCADE")
    row_key: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(16))
    column: Mapped[str | None] = mapped_column(String(128))
    old_raw: Mapped[str | None] = mapped_column(Text)
    new_raw: Mapped[str | None] = mapped_column(Text)
    old_norm: Mapped[str | None] = mapped_column(Text)
    new_norm: Mapped[str | None] = mapped_column(Text)
    annotations: Mapped[list[Any]] = mapped_column(JSONB, default=list)


# ---------------------------------------------------------------- review / ops
class ReviewItem(Base):
    __tablename__ = "review_items"
    id: Mapped[uuid.UUID] = _pk()
    kind: Mapped[str] = mapped_column(String(40), index=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(256), index=True)
    severity: Mapped[str] = mapped_column(String(8), default="medium")
    group_id: Mapped[uuid.UUID | None] = _fk("groups.id", nullable=True)
    subject_refs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    candidates: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    proposed_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    decision: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = _ts()
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class CategoryDefinition(Base):
    __tablename__ = "category_definitions"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    fields: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    matching_rules: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    scope_group_ids: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(16), default="proposed")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = _created()


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = _pk()
    type: Mapped[str] = mapped_column(String(48), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(256), unique=True)
    state: Mapped[str] = mapped_column(String(24), default="QUEUED", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_at: Mapped[datetime | None] = _ts()
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[uuid.UUID] = _pk()
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), index=True)
    table_name: Mapped[str | None] = mapped_column(String(64))
    row_id: Mapped[str | None] = mapped_column(String(64))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()
