"""Prepare images for a vision call: only what is needed, downscaled to <= 1600 px long side."""

from __future__ import annotations

import io

from PIL import Image


def media_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:3] == b"GIF":
        return "image/gif"
    return "image/jpeg"


def downscale(data: bytes, max_side: int = 1600) -> tuple[bytes, str]:
    """Return (bytes, media_type), resized only when the long side exceeds max_side."""
    with Image.open(io.BytesIO(data)) as im:
        w, h = im.size
        if max(w, h) <= max_side:
            return data, media_type(data)
        scale = max_side / max(w, h)
        small = im.convert("RGB").resize(
            (round(w * scale), round(h * scale)), Image.Resampling.LANCZOS
        )
        out = io.BytesIO()
        small.save(out, format="JPEG", quality=88)
        return out.getvalue(), "image/jpeg"
