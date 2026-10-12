"""Logical keys and album grouping (build brief 6.3/6.4)."""

from __future__ import annotations

import uuid
from collections import Counter

from app.core.time import truncate_minute
from app.ingest.envelope import MessageEnvelope
from app.ingest.text import content_fingerprint


def assign_logical_keys(group_id: uuid.UUID | str, envs: list[MessageEnvelope]) -> None:
    """logical_key = group | minute | sender_key | fingerprint | occurrence_index."""
    seen: Counter[tuple[str, str, str]] = Counter()
    for e in envs:
        minute = truncate_minute(e.sent_at).isoformat()
        fp = content_fingerprint(
            e.raw_text if e.message_type != "system" else e.raw_text,
            [a.kind or "unknown" for a in e.attachments] + [e.message_type],
        )
        ident = (minute, e.sender_key or "", fp)
        idx = seen[ident]
        seen[ident] += 1
        e.logical_key = "|".join([str(group_id), minute, e.sender_key or "", fp, str(idx)])


def mark_album_members(envs: list[MessageEnvelope]) -> list[list[int]]:
    """Group consecutive media messages from one sender in one minute after an album header."""
    groups: list[list[int]] = []
    i = 0
    while i < len(envs):
        if envs[i].message_type == "album_header":
            members = [i]
            j = i + 1
            while (
                j < len(envs)
                and envs[j].sender_key == envs[i].sender_key
                and truncate_minute(envs[j].sent_at) == truncate_minute(envs[i].sent_at)
                and envs[j].message_type == "media"
            ):
                members.append(j)
                j += 1
            groups.append(members)
            i = j
        else:
            i += 1
    return groups
