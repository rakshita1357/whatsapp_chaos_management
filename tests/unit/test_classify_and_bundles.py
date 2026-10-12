from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.providers.recorded import RecordedProvider
from app.understand.bundles import MsgLike, build_bundles
from app.understand.classify import (
    CLASSIFICATION_SCHEMA,
    ClassificationInvalid,
    ClassifyContext,
    build_inputs,
    classify,
    validate_classification,
)
from app.understand.rules import RuleEngine
from app.understand.settings import UnderstandingConfig

CFG = UnderstandingConfig()
RE = RuleEngine()


def raw(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "intent": "STATUS_UPDATE", "language": "en", "entity_mentions": [], "status_assertions": [],
        "issue_assertions": [], "job_types": [], "aggregate_claims": [],
        "refers_to_earlier_message": False, "confidence": 0.9,
    }  # fmt: skip
    base.update(kw)
    return base


def inst(span: list[int], pol: str = "positive", dim: str = "installation") -> dict[str, Any]:
    return {"dimension": dim, "value": "installed", "polarity": pol, "span": span}


def test_schema_is_strict_everywhere() -> None:
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(CLASSIFICATION_SCHEMA)


def test_ack_never_resolves_even_if_model_says_so() -> None:
    text = "Ok checking"
    v = validate_classification(raw(status_assertions=[inst([0, 2])]), text, RE.analyse(text), CFG)
    assert v.c.intent == "ACKNOWLEDGEMENT" and v.c.status_assertions == []
    assert any("acknowledgement" in i for i in v.issues)


def test_model_cannot_turn_a_real_status_into_ack_or_vice_versa() -> None:
    text = "Tv installed"
    v = validate_classification(
        raw(intent="ACKNOWLEDGEMENT", status_assertions=[inst([3, 12])]),
        text,
        RE.analyse(text),
        CFG,
    )
    # not a rule-ack, but the model called it one: assertions dropped (an ack never resolves)
    assert v.c.intent == "ACKNOWLEDGEMENT" and v.c.status_assertions == []


def test_status_must_be_attributable_to_a_span() -> None:
    text = "Tv installed in lounge"
    good = validate_classification(
        raw(status_assertions=[inst([3, 12])]), text, RE.analyse(text), CFG
    )
    assert len(good.c.status_assertions) == 1
    for bad_span in ([], [5], [50, 60], [4, 4], [-1, 3]):
        v = validate_classification(
            raw(status_assertions=[inst(bad_span)]), text, RE.analyse(text), CFG
        )
        assert v.c.status_assertions == [], bad_span
        assert any("unattributable" in i for i in v.issues)


def test_aggregates_never_create_status_events() -> None:
    text = "Yesterday - 15 installed"
    r = raw(
        intent="AGGREGATE_REPORT",
        status_assertions=[inst([12, 24])],
        aggregate_claims=[
            {
                "metric": "installations",
                "scope": "yesterday",
                "value": 15,
                "qualifier": "=",
                "note": None,
            }
        ],
    )
    v = validate_classification(r, text, RE.analyse(text), CFG)
    assert v.creates_status_events is False and v.c.status_assertions == []
    assert v.c.aggregate_claims[0].value == 15


def test_bare_pointer_states_nothing_but_live_pointer_keeps_live() -> None:
    v = validate_classification(
        raw(intent="POINTER", status_assertions=[inst([0, 2], dim="activation")]),
        "☝️", RE.analyse("☝️"), CFG,
    )  # fmt: skip
    assert v.c.status_assertions == []
    text = "LIVE☝\U0001f3fb"
    v2 = validate_classification(
        raw(intent="POINTER", status_assertions=[inst([0, 4], dim="activation")]),
        text,
        RE.analyse(text),
        CFG,
    )
    assert len(v2.c.status_assertions) == 1  # attributable to the word LIVE


def test_questions_and_unclear_carry_no_assertions() -> None:
    text = "Is the TV installed?"
    v = validate_classification(
        raw(intent="QUESTION", status_assertions=[inst([10, 19])]), text, RE.analyse(text), CFG
    )
    assert v.c.status_assertions == []


def test_low_confidence_and_clamping_and_invalid_schema() -> None:
    text = "Tv installed"
    v = validate_classification(
        raw(confidence=0.3, status_assertions=[inst([3, 12])]), text, RE.analyse(text), CFG
    )
    assert v.low_confidence is True
    assert (
        validate_classification(raw(confidence=7), text, RE.analyse(text), CFG).c.confidence == 1.0
    )
    with pytest.raises(ClassificationInvalid):
        validate_classification({"intent": "NOPE"}, text, RE.analyse(text), CFG)


def test_classify_end_to_end_with_recorded_cassette(tmp_path: Path) -> None:
    text = "TV laga diya hai"
    hints = RE.analyse(text)
    ctx = ClassifyContext("Ravi", "2026-10-09 16:21", text)
    rp = RecordedProvider(tmp_path)
    rp.record(
        "classify_message", build_inputs(ctx, hints),
        raw(language="hinglish", status_assertions=[inst([3, 13])], confidence=0.88), synthetic=True,
    )  # fmt: skip
    v = classify(rp, ctx, hints, CFG)
    assert v.c.language == "hinglish" and v.c.status_assertions[0].polarity == "positive"
    assert "laga diya" in str(build_inputs(ctx, hints)["prompt"])  # rule hint reaches the model
    assert "kal 12 installation hue" in build_inputs(ctx, hints)["system"]  # Hinglish few-shot


def test_prompt_is_deterministic() -> None:
    ctx = ClassifyContext("Ravi", "2026-10-09 16:21", "ok")
    assert build_inputs(ctx, RE.analyse("ok")) == build_inputs(ctx, RE.analyse("ok"))


# ------------------------------------------------------------------ bundles
T0 = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)


def m(
    sender: str | None, sec: int, text: str = "", media: bool = False, album: bool = False
) -> MsgLike:
    return MsgLike(object(), sender, T0 + timedelta(seconds=sec), media, text, album)


def test_bundle_image_plus_short_caption_and_forward_pointer() -> None:
    msgs = [m("a", 0, media=True), m("a", 30, "NO TV INSTALLED"), m("b", 40, "ok checking")]
    assert build_bundles(msgs, 120, 120) == [[0, 1], [2]]


def test_bundle_album_and_pointer() -> None:
    msgs = (
        [m("a", 0, album=True)] + [m("a", 5 + i, media=True) for i in range(8)] + [m("a", 20, "☝️")]
    )
    assert build_bundles(msgs, 120, 120) == [list(range(10))]  # header + 8 images + pointer


def test_bundle_boundaries() -> None:
    msgs = [m("a", 0, media=True), m("a", 300, "late text")]  # gap too large
    assert build_bundles(msgs, 120, 120) == [[0], [1]]
    long_text = "x" * 500
    msgs = [m("a", 0, media=True), m("a", 10, long_text)]  # long text is its own message
    assert build_bundles(msgs, 120, 120) == [[0], [1]]
    msgs = [m("a", 0, media=True), m("b", 10, "hi")]  # different sender
    assert build_bundles(msgs, 120, 120) == [[0], [1]]
    msgs = [m("a", 0, "hello"), m("a", 5, "again")]  # text only: never a bundle
    assert build_bundles(msgs, 120, 120) == [[0], [1]]
    assert build_bundles([m(None, 0, media=True), m(None, 5, "x")], 120, 120) == [[0], [1]]
