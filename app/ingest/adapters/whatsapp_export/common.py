"""Shared record -> envelope construction for the line-oriented text formats."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from app.core.time import from_ist_naive
from app.ingest.adapters.base import ParseResult
from app.ingest.adapters.whatsapp_export.dates import Order, build_dt, infer_date_order
from app.ingest.envelope import AttachmentRef, MessageEnvelope
from app.ingest.text import normalize_text, sender_key_for

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"}
VIDEO_EXT = {".mp4", ".3gp", ".mov", ".mkv", ".webm"}
AUDIO_EXT = {".opus", ".mp3", ".m4a", ".aac", ".ogg", ".wav"}
_FILENAME = re.compile(r"^[^\s/\\][^/\\]*\.[A-Za-z0-9]{2,5}$")
OMITTED = re.compile(
    r"^<?(?P<kind>media|image|video|audio|document|sticker|gif)s? omitted>?$", re.IGNORECASE
)
DELETED = re.compile(
    r"^(this message was deleted|you deleted this message|_this message was deleted_)$",
    re.IGNORECASE,
)
EDITED = re.compile(r"\s*<This message was edited>\s*$", re.IGNORECASE)
SYSTEM_TYPES = (
    "created group",
    "added you",
    "changed",
    "joined",
    "left",
    "removed",
    "security code",
)


def kind_from_name(name: str) -> str:
    ext = PurePosixPath(name.lower()).suffix
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    return "document"


def looks_like_filename(s: str) -> bool:
    return bool(_FILENAME.match(s.strip())) and len(s.strip()) < 256


@dataclass
class Rec:
    a: int
    b: int
    y: int
    hh: int
    mm: int
    ss: int | None
    sender: str | None  # None => system line
    lines: list[str] = field(default_factory=list)
    line_no: int = 0


def to24(hour: int, ampm: str) -> int:
    ampm = ampm.lower()
    hour %= 12
    return hour + 12 if ampm == "pm" else hour


def build_envelope(rec: Rec, order: Order, group_name: str | None) -> MessageEnvelope:
    sent = from_ist_naive(build_dt(rec.y, rec.a, rec.b, order, rec.hh, rec.mm, rec.ss or 0))
    body = "\n".join(rec.lines)
    env = MessageEnvelope(
        group_name=group_name,
        sent_at=sent,
        sent_at_precision="second" if rec.ss is not None else "minute",
        sender_label=rec.sender,
        sender_key=sender_key_for(rec.sender),
        source_line=rec.line_no,
    )
    if rec.sender is None:
        env.message_type = "system"
        env.raw_text = body
        env.normalized_text = normalize_text(body)
        return env
    first = (rec.lines[0] if rec.lines else "").strip()
    first_norm = normalize_text(first) or ""
    text = body
    if EDITED.search(text):
        env.edited = True
        text = EDITED.sub("", text)
    if DELETED.match((normalize_text(text) or "").strip()):
        env.message_type, env.deleted = "deleted", True
        return env
    if first_norm.endswith("(file attached)"):
        fname = first_norm[: -len("(file attached)")].strip()
        rest = [ln for ln in rec.lines[1:]]
        att = AttachmentRef(export_filename=fname, kind=kind_from_name(fname))
        caption = "\n".join(rest).strip()
        if caption and len(rest) == 1 and looks_like_filename(caption):
            att.original_filename = caption  # INC-13: not caption text
            caption = ""
        env.attachments.append(att)
        env.message_type = "media"
        text = caption
    elif m := OMITTED.match(first_norm):
        k = m.group("kind").lower()
        env.attachments.append(
            AttachmentRef(kind="unknown" if k == "media" else k, availability="omitted_by_export")
        )
        env.message_type = "media"
        text = "\n".join(rec.lines[1:]).strip()
    elif not body.strip():
        env.message_type = "album_header"
    env.raw_text = text if text or env.message_type != "text" else body
    env.normalized_text = normalize_text(env.raw_text)
    return env


def finalize(
    recs: list[Rec], family_default: Order, fmt: str, group_name: str | None
) -> ParseResult:
    pairs = [(r.a, r.b) for r in recs]

    def monotonic(order: Order) -> bool:
        last = None
        for r in recs:
            if r.sender is None:  # INC-03: system lines excluded
                continue
            try:
                t = build_dt(r.y, r.a, r.b, order, r.hh, r.mm, r.ss or 0)
            except ValueError:
                return False
            if last is not None and t < last:
                return False
            last = t
        return True

    res = infer_date_order(pairs, family_default, monotonic)
    out = ParseResult(
        format=fmt,
        envelopes=[],
        group_name=group_name,
        date_order=res.order,
        date_order_evidence=res.evidence,
        needs_review=res.needs_review,
    )
    if res.order is None:
        out.warnings.append("date order ambiguous; messages not built (NEEDS_REVIEW)")
        return out
    out.envelopes = [build_envelope(r, res.order, group_name) for r in recs]
    return out
