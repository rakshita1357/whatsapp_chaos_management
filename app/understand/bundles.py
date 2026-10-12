"""Evidence bundles (build brief 7.4): consecutive messages from one sender within a short gap
that are attachments plus short text are one bundle for one event."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class MsgLike:
    id: object
    sender_key: str | None
    sent_at: datetime
    has_media: bool
    text: str
    is_album_header: bool = False


def build_bundles(msgs: list[MsgLike], window_seconds: int, short_chars: int) -> list[list[int]]:
    """Return groups of indexes into `msgs` (input must be in sent order)."""
    out: list[list[int]] = []
    i = 0
    n = len(msgs)
    while i < n:
        group = [i]
        j = i + 1
        while j < n and _joins(msgs, group, j, window_seconds, short_chars):
            group.append(j)
            j += 1
        # a run without any media is not a bundle: each text message stands alone
        if len(group) > 1 and not any(msgs[k].has_media or msgs[k].is_album_header for k in group):
            group = [i]
            j = i + 1
        out.append(group)
        i = j
    return out


def _joins(msgs: list[MsgLike], group: list[int], j: int, window: int, short: int) -> bool:
    prev, cur = msgs[group[-1]], msgs[j]
    if cur.sender_key != msgs[group[0]].sender_key or cur.sender_key is None:
        return False
    if (cur.sent_at - prev.sent_at).total_seconds() > window:
        return False
    text_only = not cur.has_media and not cur.is_album_header
    if text_only and len(cur.text) > short:
        return False
    first = msgs[group[0]]
    first_text_only = not first.has_media and not first.is_album_header
    # two plain short texts alone are not a bundle; the caller checks that a run has media
    return not (first_text_only and len(first.text) > short)
