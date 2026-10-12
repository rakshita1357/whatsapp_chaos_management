"""Reply-link scoring on synthetic scenarios modelled on the build brief's golden constraints.
Real-fixture precision/recall must be reported in PROGRESS.md once fixtures are available."""

import random
import uuid
from datetime import UTC, datetime, timedelta

from app.understand.reply_links import (
    LinkInput,
    LinkWeights,
    Thread,
    decide,
    evaluate_links,
    link_message,
    load_weights,
    score_threads,
)

W = load_weights()
D = datetime(2026, 10, 9, tzinfo=UTC)


def at(h: int, m: int) -> datetime:
    return D + timedelta(hours=h, minutes=m)


def chok(state: str = "not_installed", **kw: object) -> Thread:
    return Thread(
        "CHOK",
        "3007530-Sv&Pa-Chokidhani-RdrMtr",
        state,
        kw.pop("last", at(15, 40)),  # type: ignore[arg-type]
        identifiers={"3007530-sv&pa-chokidhani-rdrmtr", "chokidhani", "3007530"},
        **kw,
    )  # type: ignore[arg-type]


def him(state: str = "not_installed", **kw: object) -> Thread:
    return Thread(
        "HIM",
        "300A490-Sv&Pa-Himatnagar",
        state,
        kw.pop("last", at(14, 20)),  # type: ignore[arg-type]
        identifiers={"300a490", "himatnagar"},
        **kw,
    )  # type: ignore[arg-type]


def say_installed(h: int, m: int, sender: str = "ravi", **kw: object) -> LinkInput:
    return LinkInput(sender, at(h, m), "status", "installation", "positive", **kw)  # type: ignore[arg-type]


def test_lounge_message_ranks_himatnagar_first_never_chokidhani() -> None:
    # Ravi acknowledged the Himatnagar request ("Ok checking") earlier; nobody asked him about Chokidhani
    threads = [chok(last=at(14, 5)), him(acked_by={"ravi"})]
    d = link_message(say_installed(14, 53), threads, W)
    assert d.top is not None and d.top.key == "HIM"
    assert d.kind == "review"  # 0.30 ack + 0.25 fit + recency < 0.80: not automatic
    assert d.ranked[0].score > d.ranked[1].score


def test_renovation_goes_to_chokidhani_never_himatnagar() -> None:
    threads = [chok(assigned_to={"ravi"}, acked_by={"ravi"}), him("installed", last=at(14, 53))]
    inp = LinkInput("ravi", at(16, 19), "issue")
    d = link_message(inp, threads, W)
    assert d.top is not None and d.top.key == "CHOK"
    assert d.kind in ("auto", "review")
    if d.kind == "auto":
        assert d.top.key == "CHOK"
    himatnagar = next(c for c in d.ranked if c.key == "HIM")
    assert himatnagar.score < d.top.score  # an installed site fits a blocker poorly


def test_native_only_tv_installed_is_never_auto_linked_to_chokidhani() -> None:
    # 16:21 "Tv installed": both threads still open and Ravi touched both -> ambiguous
    threads = [chok(acked_by={"ravi"}, last=at(16, 19)), him(acked_by={"ravi"}, last=at(16, 10))]
    d = link_message(say_installed(16, 21), threads, W)
    assert d.kind == "review"
    assert len(d.ranked) == 2 and d.ranked[0].score - d.ranked[1].score < W.auto_link_min_margin


def test_attachment_evidence_can_make_a_link_automatic() -> None:
    threads = [chok(acked_by={"ravi"}), him(acked_by={"ravi"})]
    inp = say_installed(16, 0, attachment_text=["Himatnagar, Gujarat 383001"])
    d = link_message(inp, threads, W)
    assert d.kind == "auto" and d.top is not None and d.top.key == "HIM"
    assert any("attachment evidence" in r for r in d.top.reasons)


def test_margin_rule_blocks_auto_when_two_candidates_are_close() -> None:
    inp = say_installed(16, 0, attachment_text=["Chokidhani and Himatnagar both"])
    d = link_message(inp, [chok(acked_by={"ravi"}), him(acked_by={"ravi"})], W)
    assert d.kind == "review"  # both score high; margin < 0.25


def test_replied_penalty_only_when_another_candidate_unanswered() -> None:
    inp = say_installed(16, 0)
    with_other = score_threads(inp, [chok(substantive_by={"ravi"}), him()], W)
    solo = score_threads(inp, [chok(substantive_by={"ravi"})], W)
    c_with = next(c for c in with_other if c.key == "CHOK")
    assert any("already replied" in r for r in c_with.reasons)
    assert not any("already replied" in r for r in solo[0].reasons)


def test_window_excludes_old_threads() -> None:
    old = chok(last=at(2, 0))
    assert score_threads(say_installed(16, 0), [old], W) == []
    assert link_message(say_installed(16, 0), [old], W).kind == "none"


def test_thresholds_come_from_config() -> None:
    strict = LinkWeights(auto_link_min_score=1.5)  # unreachable on purpose
    inp = say_installed(16, 0, attachment_text=["Himatnagar"])
    assert link_message(inp, [him(acked_by={"ravi"})], W).kind == "auto"
    assert link_message(inp, [him(acked_by={"ravi"})], strict).kind == "review"
    assert W.auto_link_min_score == 0.80 and W.auto_link_min_margin == 0.25


def test_invariant_no_auto_link_without_score_and_margin() -> None:
    rnd = random.Random(7)
    for _ in range(2000):
        threads = [
            Thread(
                f"t{i}", f"t{i}", rnd.choice(["not_installed", "installed", "unknown", "no_screen"]),
                at(16, 0) - timedelta(minutes=rnd.randint(0, 400)),
                acked_by={"s"} if rnd.random() < 0.5 else set(),
                assigned_to={"s"} if rnd.random() < 0.3 else set(),
                substantive_by={"s"} if rnd.random() < 0.3 else set(),
                identifiers={"abc"} if rnd.random() < 0.3 else set(),
            )
            for i in range(rnd.randint(0, 5))
        ]  # fmt: skip
        inp = LinkInput("s", at(16, 0), rnd.choice(["status", "issue", "other"]), "installation",
                        rnd.choice(["positive", "negative"]), ["abc"] if rnd.random() < 0.3 else [])  # fmt: skip
        d = link_message(inp, threads, W)
        if d.kind == "auto":
            second = d.ranked[1].score if len(d.ranked) > 1 else 0.0
            assert d.ranked[0].score >= W.auto_link_min_score
            assert d.ranked[0].score - second >= W.auto_link_min_margin
        assert decide(d.ranked, W).kind == d.kind


def test_precision_recall_report() -> None:
    cases = [
        (link_message(say_installed(14, 53), [chok(last=at(14, 5)), him(acked_by={"ravi"})], W), "HIM"),
        (link_message(LinkInput("ravi", at(16, 19), "issue"), [chok(acked_by={"ravi"}, assigned_to={"ravi"}), him("installed")], W), "CHOK"),
        (link_message(say_installed(16, 0, attachment_text=["Himatnagar"]), [chok(acked_by={"ravi"}), him(acked_by={"ravi"})], W), "HIM"),
    ]  # fmt: skip
    r = evaluate_links(cases)
    assert r["wrong_auto_links"] == 0 and r["auto_precision"] == 1.0
    assert r["review_correct_first"] + r["auto_links"] == 3  # every case right-first
    assert str(uuid.uuid4())  # keep uuid import used for entity ids in later tests
