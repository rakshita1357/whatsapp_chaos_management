import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import pytest
from PIL import Image

from app.core.config import Settings
from app.providers.anthropic import AnthropicProvider
from app.providers.base import ProviderError, request_key
from app.providers.images import downscale
from app.providers.recorded import CassetteMissing, RecordedProvider, RecordingProvider

SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}


def test_request_key_is_stable_and_input_sensitive() -> None:
    a = request_key("t", {"prompt": "hi", "n": 1})
    assert a == request_key("t", {"n": 1, "prompt": "hi"})  # key order irrelevant
    assert a != request_key("t", {"prompt": "hi!", "n": 1})
    assert a != request_key("u", {"prompt": "hi", "n": 1})
    assert request_key("t", {}, [b"img"]) != request_key("t", {}, [b"other"])


def test_recorded_roundtrip_and_missing(tmp_path: Path) -> None:
    rp = RecordedProvider(tmp_path)
    with pytest.raises(CassetteMissing):
        rp.complete_json("t", SCHEMA, {"prompt": "p"})
    path = rp.record("t", {"prompt": "p"}, {"x": 1}, synthetic=True)
    assert json.loads(path.read_text())["synthetic"] is True
    out, usage, model = rp.complete_json("t", SCHEMA, {"prompt": "p"})
    assert out == {"x": 1} and usage.input_tokens == 0 and model == "recorded"


def test_recording_wrapper_writes_cassette(tmp_path: Path) -> None:
    class Inner:
        def complete_json(self, task, schema, inputs, images=None):  # type: ignore[no-untyped-def]
            from app.providers.base import Usage

            return {"x": 7}, Usage(3, 4), "real-model"

    rec = RecordingProvider(Inner(), tmp_path)  # type: ignore[arg-type]
    rec.complete_json("t", SCHEMA, {"prompt": "p"})
    out, usage, model = RecordedProvider(tmp_path).complete_json("t", SCHEMA, {"prompt": "p"})
    assert out == {"x": 7} and usage.output_tokens == 4 and model == "real-model"


def test_downscale_only_when_needed() -> None:
    def png(w: int, h: int) -> bytes:
        b = io.BytesIO()
        Image.new("RGB", (w, h), "white").save(b, "PNG")
        return b.getvalue()

    small, mt = downscale(png(800, 600))
    assert mt == "image/png" and Image.open(io.BytesIO(small)).size == (800, 600)
    big, mt = downscale(png(3200, 1600))
    assert mt == "image/jpeg" and max(Image.open(io.BytesIO(big)).size) == 1600


def _resp(text: str = '{"x": 1}', stop: str = "end_turn", model: str = "m") -> Any:
    return SimpleNamespace(
        stop_reason=stop, stop_details=None, model=model,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0),
    )  # fmt: skip


class FakeClient:
    def __init__(self, resp: Any, beta_error: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._resp, self._beta_error = resp, beta_error
        outer = self

        class Msgs:
            def create(self, **kw: Any) -> Any:
                outer.calls.append(("plain", kw))
                return outer._resp

        class BetaMsgs:
            def create(self, **kw: Any) -> Any:
                outer.calls.append(("beta", kw))
                if outer._beta_error:
                    raise outer._beta_error
                return outer._resp

        self.messages = Msgs()
        self.beta = SimpleNamespace(messages=BetaMsgs())


def _provider(client: Any) -> AnthropicProvider:
    return AnthropicProvider(Settings(anthropic_api_key="k"), client=client)


def test_anthropic_request_shape_and_models() -> None:
    c = FakeClient(_resp())
    p = _provider(c)
    out, usage, model = p.complete_json("classify_message", SCHEMA, {"system": "S", "prompt": "P"})
    assert out == {"x": 1} and usage.input_tokens == 10 and model == "m"
    kind, kw = c.calls[0]
    assert kind == "plain"  # haiku: no server-side fallback
    assert kw["model"] == "claude-haiku-5-5" and kw["system"] == "S"
    assert kw["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
    assert "temperature" not in kw and "tool_choice" not in kw  # rejected on newer models
    assert p.model_for("extract_image") == "claude-sonnet-5-5"
    assert p.model_for("assistant") == "claude-sonnet-5-5"


def test_vision_call_uses_beta_fallback_and_images() -> None:
    c = FakeClient(_resp())
    b = io.BytesIO()
    Image.new("RGB", (10, 10)).save(b, "PNG")
    _provider(c).complete_json("extract_image", SCHEMA, {"prompt": "P"}, [b.getvalue()])
    kind, kw = c.calls[0]
    assert kind == "beta" and kw["fallbacks"] == "default"
    block = kw["messages"][0]["content"][0]
    assert block["type"] == "image" and block["source"]["media_type"] == "image/png"


def test_beta_rejection_retries_without_fallback() -> None:
    err = anthropic.BadRequestError(
        "unknown beta",
        response=SimpleNamespace(status_code=400, headers={}, request=None),
        body=None,  # type: ignore[arg-type]
    )
    c = FakeClient(_resp(), beta_error=err)
    out, _, _ = _provider(c).complete_json("extract_image", SCHEMA, {"prompt": "P"})
    assert out == {"x": 1} and [k for k, _ in c.calls] == ["beta", "plain"]


@pytest.mark.parametrize(
    ("resp", "msg"),
    [
        (_resp(stop="refusal"), "refused"),
        (_resp(stop="max_tokens"), "truncated"),
        (_resp(text="not json"), "invalid JSON"),
    ],
)
def test_unusable_responses_raise(resp: Any, msg: str) -> None:
    with pytest.raises(ProviderError, match=msg):
        _provider(FakeClient(resp)).complete_json("classify_message", SCHEMA, {"prompt": "P"})


def test_missing_key_raises() -> None:
    with pytest.raises(ProviderError):
        AnthropicProvider(Settings(anthropic_api_key=None))
