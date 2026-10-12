from __future__ import annotations

import re
from pathlib import Path

from app.ingest.adapters.whatsapp_export import alt_md, alt_txt, android_native

_NATIVE_NAME = re.compile(r"^WhatsApp Chat with (.+)\.txt$")


def detect_format(text: str) -> str | None:
    head = [ln for ln in text.splitlines()[:60] if ln.strip()]
    if any(ln.startswith("# WhatsApp Chat Export") or alt_md.DAY.match(ln) for ln in head):
        return "alt_md"
    if any(alt_txt.HEADER.match(ln) for ln in head):
        return "alt_txt"
    if any(android_native.HEADER.match(ln) for ln in head):
        return "android_native"
    return None


def group_name_from_filename(path: str | Path) -> str | None:
    m = _NATIVE_NAME.match(Path(path).name)
    return m.group(1) if m else None


PARSERS = {"android_native": android_native.parse, "alt_txt": alt_txt.parse, "alt_md": alt_md.parse}
