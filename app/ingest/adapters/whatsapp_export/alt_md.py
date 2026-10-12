"""Markdown export: date headers, `[1:27 PM] **Sender:**` lines, `> _Sender: text_` quotes."""

from __future__ import annotations

import re
from datetime import datetime

from app.core.time import from_ist_naive
from app.ingest.adapters.base import ParseResult
from app.ingest.adapters.whatsapp_export.common import (
    DELETED,
    EDITED,
    OMITTED,
    kind_from_name,
    looks_like_filename,
)
from app.ingest.envelope import AttachmentRef, MessageEnvelope
from app.ingest.text import normalize_text, sender_key_for

TITLE = re.compile(r"^#\s+WhatsApp Chat Export:\s*(.+?)\s*$")
DAY = re.compile(r"^##\s+([A-Za-z]+ \d{1,2}, \d{4})\s*$")
MSG = re.compile(r"^\[(\d{1,2}):(\d{2})\s*([AP]M)\]\s+(?:\*\*(.+?):\*\*|__system__:?)\s*(.*)$")
MEDIA_TAG = re.compile(r"^\[(Image|Video|Document|Audio)\]\s*(.*)$", re.IGNORECASE)


def parse(text: str, group_name: str | None = None) -> ParseResult:
    out = ParseResult(format="alt_md", envelopes=[], date_order="MDY")
    out.date_order_evidence = {"reason": "month names are unambiguous"}
    day: datetime | None = None
    cur: dict | None = None  # type: ignore[type-arg]
    quote: list[str] = []

    def flush() -> None:
        nonlocal cur
        if cur is not None:
            out.envelopes.append(_build(cur, out.group_name))
            cur = None

    for n, line in enumerate(text.splitlines(), 1):
        if t := TITLE.match(line):
            out.group_name = t.group(1)
            continue
        if d := DAY.match(line):
            flush()
            day = datetime.strptime(d.group(1), "%B %d, %Y")
            continue
        if (m := MSG.match(line)) and day is not None:
            flush()
            hh, mm, ap, sender, rest = m.groups()
            h = int(hh) % 12 + (12 if ap == "PM" else 0)
            cur = {
                "dt": day.replace(hour=h, minute=int(mm)),
                "sender": sender,
                "lines": [rest] if rest else [],
                "quote": [],
                "line": n,
            }
            quote = cur["quote"]
            continue
        if cur is None:
            continue
        if line.startswith(">"):
            quote.append(line[1:].strip().strip("_"))
        else:
            cur["lines"].append(line)
    flush()
    if group_name and not out.group_name:
        out.group_name = group_name
    for e in out.envelopes:
        e.group_name = out.group_name
    return out


def _build(c: dict, group: str | None) -> MessageEnvelope:  # type: ignore[type-arg]
    sender = c["sender"]
    env = MessageEnvelope(
        group_name=group,
        sent_at=from_ist_naive(c["dt"]),
        sent_at_precision="minute",
        sender_label=sender,
        sender_key=sender_key_for(sender),
        source_line=c["line"],
        quoted_context=" ".join(c["quote"]).strip() or None,
    )
    lines = [ln for ln in c["lines"]]
    while lines and not lines[-1].strip():
        lines.pop()
    body = "\n".join(lines).strip()
    if sender is None:
        env.message_type, env.raw_text = "system", body
        env.normalized_text = normalize_text(body)
        return env
    if "[album message]" in body:
        env.message_type = "album_header"
        body = body.replace("[album message]", "").strip()
    env.forwarded = True if "[Forwarded]" in body else None
    body = body.replace("[Forwarded]", "").strip()
    if EDITED.search(body):
        env.edited, body = True, EDITED.sub("", body)
    if DELETED.match(body.strip("_ ").strip()) or "This message was deleted" in body:
        env.message_type, env.deleted = "deleted", True
        return env
    if mt := MEDIA_TAG.match(body):
        kind = mt.group(1).lower()
        rest = mt.group(2).strip()
        att = AttachmentRef(
            kind=kind if kind != "document" else "document", availability="omitted_by_export"
        )
        if rest and looks_like_filename(rest):
            att.original_filename, att.kind = rest, kind_from_name(rest)
            rest = ""
        env.attachments.append(att)
        env.message_type = "media"
        body = rest
    elif OMITTED.match(body):
        env.attachments.append(AttachmentRef(kind="unknown", availability="omitted_by_export"))
        env.message_type, body = "media", ""
    env.raw_text = body
    env.normalized_text = normalize_text(body)
    return env
