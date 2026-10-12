"""Connector-independent message envelope. Missing fields are None, never guessed."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Availability = Literal["present", "omitted_by_export", "missing_file"]
MessageType = Literal["text", "media", "system", "deleted", "album_header"]


class AttachmentRef(BaseModel):
    export_filename: str | None = None
    original_filename: str | None = None
    kind: str | None = None  # image / video / document / audio / unknown (from marker or extension)
    availability: Availability = "present"


class MessageEnvelope(BaseModel):
    group_name: str | None = None
    sent_at: datetime  # tz-aware UTC
    sent_at_precision: Literal["minute", "second"] = "minute"
    sender_label: str | None = None
    sender_key: str | None = None
    raw_text: str | None = None
    normalized_text: str | None = None
    message_type: MessageType = "text"
    forwarded: bool | None = None  # tri-state
    quoted_context: str | None = None
    edited: bool = False
    deleted: bool = False
    attachments: list[AttachmentRef] = Field(default_factory=list)
    logical_key: str | None = None
    provider_message_id: str | None = None
    source_line: int | None = None
