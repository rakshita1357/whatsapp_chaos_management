from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.config import load_yaml


@dataclass
class UnderstandingConfig:
    extractor_version: str = "m3-1"
    min_confidence: float = 0.5
    bundle_window_seconds: int = 120
    bundle_short_text_chars: int = 120
    context_messages: int = 5


def load_understanding_config(config_dir: Path | None = None) -> UnderstandingConfig:
    raw = load_yaml("understanding", config_dir) or {}
    return UnderstandingConfig(
        **{k: v for k, v in raw.items() if k in UnderstandingConfig.__annotations__}
    )
