"""Replay (and record) model responses as cassettes: tests/cassettes/<task>/<sha256>.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.providers.base import ModelProvider, ProviderError, Usage, request_key


class CassetteMissing(ProviderError):
    def __init__(self, task: str, key: str, path: Path) -> None:
        super().__init__(f"no cassette for task={task!r} key={key[:12]}… expected at {path}")
        self.task, self.key, self.path = task, key, path


class RecordedProvider:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, task: str, key: str) -> Path:
        return self.root / task / f"{key}.json"

    def complete_json(
        self,
        task: str,
        schema: dict[str, Any],
        inputs: dict[str, Any],
        images: list[bytes] | None = None,
    ) -> tuple[dict[str, Any], Usage, str]:
        key = request_key(task, inputs, images)
        path = self.path_for(task, key)
        if not path.exists():
            raise CassetteMissing(task, key, path)
        data = json.loads(path.read_text(encoding="utf-8"))
        u = data.get("usage") or {}
        return (
            data["output"],
            Usage(
                u.get("input_tokens", 0), u.get("output_tokens", 0), u.get("cache_read_tokens", 0)
            ),
            data.get("model_id", "recorded"),
        )

    def record(
        self,
        task: str,
        inputs: dict[str, Any],
        output: dict[str, Any],
        images: list[bytes] | None = None,
        *,
        model_id: str = "recorded",
        usage: Usage | None = None,
        synthetic: bool = False,
    ) -> Path:
        key = request_key(task, inputs, images)
        path = self.path_for(task, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        doc: dict[str, Any] = {
            "task": task,
            "model_id": model_id,
            "output": output,
            "usage": (usage or Usage()).as_json(),
            "synthetic": synthetic,  # hand-written from expectations, not a real model response
            "input_preview": {k: (str(v)[:200]) for k, v in inputs.items()},
        }
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        return path


class RecordingProvider:
    """Wraps a real provider and writes each response as a cassette (`make record`)."""

    def __init__(self, inner: ModelProvider, root: Path) -> None:
        self.inner = inner
        self.recorder = RecordedProvider(root)

    def complete_json(
        self,
        task: str,
        schema: dict[str, Any],
        inputs: dict[str, Any],
        images: list[bytes] | None = None,
    ) -> tuple[dict[str, Any], Usage, str]:
        out, usage, model_id = self.inner.complete_json(task, schema, inputs, images)
        self.recorder.record(task, inputs, out, images, model_id=model_id, usage=usage)
        return out, usage, model_id
