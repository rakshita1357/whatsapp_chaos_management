"""Model provider interface (build brief 7.5). Everything a model returns is a proposal."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    def as_json(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
        }


class ProviderError(Exception):
    """The model call failed or returned something unusable. Callers retry or send to review."""


class ModelProvider(Protocol):
    def complete_json(
        self,
        task: str,
        schema: dict[str, Any],
        inputs: dict[str, Any],
        images: list[bytes] | None = None,
    ) -> tuple[dict[str, Any], Usage, str]:
        """Return (json_output, usage, model_id). `inputs` holds `system` and `prompt` text plus any
        structured context; images are raw bytes (downscaled by the provider if too large)."""
        ...


def request_key(task: str, inputs: dict[str, Any], images: list[bytes] | None = None) -> str:
    """Stable cassette key: sha256 of the canonical JSON of task + inputs + image hashes."""
    payload = {
        "task": task,
        "inputs": inputs,
        "images": [hashlib.sha256(i).hexdigest() for i in (images or [])],
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
