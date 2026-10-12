"""Text normalization and keys shared by all adapters (INC-04, INC-06, INC-16)."""

from __future__ import annotations

import hashlib
import re
import unicodedata

_INVISIBLE = dict.fromkeys(map(ord, "⁨⁩‎‏﻿‪‬"))
_MARKERS = re.compile(
    r"<Media omitted>|<image omitted>|<video omitted>|<This message was edited>|"
    r"\[Forwarded\]|\[Image\]|\[Video\]|\[Document\]|\[album message\]|\(file attached\)",
    re.IGNORECASE,
)


def normalize_text(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = unicodedata.normalize("NFC", raw.translate(_INVISIBLE).replace(" ", " "))
    return s.replace(" ", " ").strip()


def strip_markers(text: str | None) -> str:
    return re.sub(r"\s+", " ", _MARKERS.sub("", text or "")).strip()


def sender_key_for(label: str | None) -> str | None:
    """Phone label -> E.164 digits; name-only label -> slug; None stays None."""
    if not label:
        return None
    norm = normalize_text(label) or ""
    digits = re.sub(r"\D", "", norm)
    if norm.lstrip().startswith("+") or (len(digits) >= 10 and re.fullmatch(r"[+\d\s()-]+", norm)):
        return digits
    return re.sub(r"[^a-z0-9]+", "-", norm.casefold()).strip("-") or None


def content_fingerprint(text: str | None, attachment_kinds: list[str]) -> str:
    base = strip_markers(normalize_text(text)).casefold()
    payload = base + "|" + ",".join(sorted(attachment_kinds))
    return hashlib.sha1(payload.encode()).hexdigest()  # noqa: S324 - identity, not security
