"""Anthropic provider: structured JSON via `output_config.format` (no forced tool_choice, which
newer models reject). Token usage is logged, never message text."""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

import anthropic

from app.core.config import Settings, get_settings
from app.providers.base import ProviderError, Usage
from app.providers.images import downscale

log = logging.getLogger(__name__)

# Models whose refusals can be rescued server-side (Claude API only). If the beta is rejected we
# transparently retry without it, so a wrong/unsupported header never breaks extraction.
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

TASK_MODEL_ATTR = {
    "classify_message": "text_model",
    "extract_image": "vision_model",
    "extract_pdf_page": "vision_model",
    "assistant": "assistant_model",
}


class AnthropicProvider:
    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        if client is None:
            if not self.settings.anthropic_api_key:
                raise ProviderError("ANTHROPIC_API_KEY is not set")
            client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
        self.client = client

    def model_for(self, task: str) -> str:
        return str(getattr(self.settings, TASK_MODEL_ATTR.get(task, "text_model")))

    def complete_json(
        self,
        task: str,
        schema: dict[str, Any],
        inputs: dict[str, Any],
        images: list[bytes] | None = None,
    ) -> tuple[dict[str, Any], Usage, str]:
        model = self.model_for(task)
        content: list[dict[str, Any]] = []
        for raw in images or []:
            data, mt = downscale(raw, self.settings.image_max_side_px)
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mt,
                        "data": base64.standard_b64encode(data).decode(),
                    },
                }
            )
        content.append({"type": "text", "text": str(inputs.get("prompt", ""))})
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": 4096,
            "system": str(inputs.get("system", "")),
            "messages": [{"role": "user", "content": content}],
            "output_config": {
                "format": {"type": "json_schema", "schema": schema},
                "effort": "low",
            },
        }
        resp = self._create(model, kwargs)
        if resp.stop_reason == "refusal":
            raise ProviderError(
                f"model refused (category={getattr(resp.stop_details, 'category', None)})"
            )
        if resp.stop_reason == "max_tokens":
            raise ProviderError("model output truncated (max_tokens)")
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if text is None:
            raise ProviderError("no text block in model response")
        try:
            out = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"model returned invalid JSON: {exc}") from exc
        u = resp.usage
        usage = Usage(
            getattr(u, "input_tokens", 0) or 0,
            getattr(u, "output_tokens", 0) or 0,
            getattr(u, "cache_read_input_tokens", 0) or 0,
        )
        log.info(
            "model call task=%s model=%s in=%d out=%d",
            task, resp.model, usage.input_tokens, usage.output_tokens,
        )  # fmt: skip
        return out, usage, str(resp.model)

    def _create(self, model: str, kwargs: dict[str, Any]) -> Any:
        try:
            if model in _FALLBACK_MODELS:
                return self.client.beta.messages.create(
                    betas=[_FALLBACK_BETA], fallbacks="default", **kwargs
                )
            return self.client.messages.create(**kwargs)
        except (anthropic.BadRequestError, TypeError) as exc:
            if model in _FALLBACK_MODELS:
                log.warning(
                    "fallback-enabled call rejected (%s); retrying without it", type(exc).__name__
                )
                try:
                    return self.client.messages.create(**kwargs)
                except anthropic.APIError as inner:
                    raise ProviderError(f"{type(inner).__name__}: {inner}") from inner
            raise ProviderError(f"{type(exc).__name__}: {exc}") from exc
        except anthropic.APIError as exc:
            raise ProviderError(f"{type(exc).__name__}: {exc}") from exc
