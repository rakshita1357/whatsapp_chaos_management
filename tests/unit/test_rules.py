import pytest

from app.understand.rules import RuleEngine, is_acknowledgement, is_pointer

RE = RuleEngine()


@pytest.mark.parametrize(
    "text",
    ["ok", "Ok.", "OKAY!", "noted", "will check", "Checking", "ok will check", "ok checking",
     "\U0001f44d", "\U0001f44d\U0001f3fd", "ok \U0001f44d", "Ok sir", "noted, checking."],
)  # fmt: skip
def test_acknowledgements(text: str) -> None:
    assert is_acknowledgement(text)


@pytest.mark.parametrize(
    "text",
    ["ok installed", "Tv installed", "not installed", "check the TV", "ok but no tv", "", "👆"],
)
def test_not_acknowledgements(text: str) -> None:
    assert not is_acknowledgement(text)


@pytest.mark.parametrize("text", ["☝️", "👆", "☝\U0001f3fb", "LIVE☝\U0001f3fb", "LIVE ☝️", "☝️"])
def test_pointers(text: str) -> None:
    assert is_pointer(text)
    assert not is_acknowledgement(text)  # a pointer is not an ack and not a status


def test_not_pointers() -> None:
    assert not is_pointer("Tv installed")
    assert not is_pointer("this one is the one that is live and working ☝️ ok")


def test_codes_division_dealer_sol_serial_phone_pin() -> None:
    t = (
        "3007530-Sv&Pa-Chokidhani-RdrMtr not installed. Dealer 300A490, call 98765 43210 "
        "Sol ID: 7CNA serial AB12CD34EF56 address Near Mall Road 800001"
    )
    h = RE.analyse(t)
    assert [x.value for x in h.division_codes] == ["3007530-Sv&Pa-Chokidhani-RdrMtr"]
    assert [x.value for x in h.dealer_codes] == ["300A490"]  # not the one inside the division code
    assert [x.value for x in h.sol_ids] == ["7CNA"]
    assert [x.value for x in h.serials] == ["AB12CD34EF56"]
    assert [x.value for x in h.phones] == ["9876543210"]
    assert [x.value for x in h.pins] == ["800001"]


def test_pin_needs_address_context_and_is_not_a_phone_fragment() -> None:
    assert RE.analyse("count is 123456 today").pins == []
    assert RE.analyse("pincode 682001").pins[0].value == "682001"


def test_status_keywords_longest_first() -> None:
    kws = [k.value for k in RE.analyse("NO TV INSTALLED at site, renovation going on").keywords]
    assert kws == ["no tv installed", "renovation"]
    assert [k.value for k in RE.analyse("Tv installed in lounge").keywords] == ["installed"]
    assert [k.value for k in RE.analyse("relocating the store").keywords] == ["relocating"]
    assert [k.value for k in RE.analyse("TV laga diya hai").keywords] == ["laga diya"]  # Hinglish


def test_mentions_use_raw_text_with_isolates() -> None:
    raw = "@⁨Ravi Kumar⁩ please check, @+91 88489 91765 also"
    h = RE.analyse("@Ravi Kumar please check, @+91 88489 91765 also", raw)
    assert [(m.value, k) for m, k in zip(h.mentions, h.mention_kinds, strict=True)] == [
        ("Ravi Kumar", "name"),
        ("+91 88489 91765", "phone"),
    ]


def test_aggregate_candidates_not_events() -> None:
    h = RE.analyse(
        "Yesterday - 15 installed, today: so far 7, more than 100 done, kal 12 installation hue"
    )
    got = [(a.scope, a.qualifier, a.value) for a in h.aggregates]
    assert ("yesterday", "=", 15) in got and ("today", "=", 7) in got
    assert ("program", ">", 100) in got and ("yesterday", "=", 12) in got


def test_codes_come_from_config_not_hardcoded() -> None:
    eng = RuleEngine({"clients": {"tata": {"dealer_code_regex": "^ZZ\\d{3}$"}}})
    assert [x.value for x in eng.analyse("see ZZ123").dealer_codes] == ["ZZ123"]
    assert eng.analyse("see 300A490").dealer_codes == []
