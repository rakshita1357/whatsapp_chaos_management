"""Reply/thread linking (build brief 7.3, INC-08). The invariant is ZERO wrong auto-links:
a correct link may be automatic, or may go to review with the correct candidate ranked first.
Weights live in config/reply_weights.yaml, never in code."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import load_yaml

# thread states that a "now installed" report or a blocker plausibly follows
OPEN_STATES = {"not_installed", "no_screen", "issue_open", "pending_install", "planned"}
BLOCKER_FITS = {"not_installed", "no_screen", "issue_open", "pending_install"}


@dataclass
class LinkWeights:
    window_hours: float = 6
    ack_by_sender: float = 0.30
    assigned_to_sender: float = 0.20
    status_compat: float = 0.25
    recency: float = 0.15
    recency_half_life_min: float = 60
    attachment_match: float = 0.40
    already_replied_penalty: float = -0.40
    auto_link_min_score: float = 0.80
    auto_link_min_margin: float = 0.25


def load_weights(config_dir: Path | None = None) -> LinkWeights:
    raw: dict[str, Any] = load_yaml("reply_weights", config_dir) or {}
    return LinkWeights(**{k: v for k, v in raw.items() if k in LinkWeights.__annotations__})


@dataclass
class Thread:
    key: str  # entity id (str) or event id when the entity is unresolved
    label: str
    last_state: (
        str  # not_installed | no_screen | issue_open | installed | pending_install | unknown
    )
    last_activity: datetime
    entity_id: uuid.UUID | None = None
    acked_by: set[str] = field(default_factory=set)  # sender_keys that acknowledged this thread
    assigned_to: set[str] = field(default_factory=set)  # sender_keys @-assigned on it
    substantive_by: set[str] = field(default_factory=set)  # senders with a non-ack reply on it
    identifiers: set[str] = field(default_factory=set)  # code/town/pin strings for attachment match
    last_message_id: uuid.UUID | None = None  # newest message on the thread (reply_to target)


@dataclass
class LinkInput:
    sender_key: str | None
    sent_at: datetime
    kind: str  # status | issue | other
    dimension: str | None = None  # installation | activation | readiness
    polarity: str | None = None  # positive | negative
    attachment_text: list[str] = field(default_factory=list)  # vision/geo-stamp text (M4)


@dataclass
class Candidate:
    key: str
    entity_id: uuid.UUID | None
    label: str
    score: float
    reasons: list[str]

    def as_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "entity_id": str(self.entity_id) if self.entity_id else None,
            "label": self.label,
            "score": self.score,
            "reasons": self.reasons,
        }


@dataclass
class Decision:
    kind: str  # auto | review | none
    ranked: list[Candidate]

    @property
    def top(self) -> Candidate | None:
        return self.ranked[0] if self.ranked else None


def status_compat(thread: Thread, inp: LinkInput) -> float:
    """How well the message fits the thread's last state (0..1)."""
    s = thread.last_state
    if inp.kind == "issue":
        return 1.0 if s in BLOCKER_FITS else (0.5 if s == "unknown" else 0.4)
    if inp.dimension == "installation" and inp.polarity == "positive":
        if s in BLOCKER_FITS:
            return 1.0
        return 0.3 if s == "unknown" else 0.0  # already installed: a repeat fits poorly
    if inp.dimension == "installation" and inp.polarity == "negative":
        return 0.5 if s in ("unknown", "installed") else 0.8
    if inp.dimension == "activation":
        return 0.6 if s in ("installed", "pending_install") else 0.4
    return 0.3


def score_threads(inp: LinkInput, threads: list[Thread], w: LinkWeights) -> list[Candidate]:
    cands: list[Candidate] = []
    window_s = w.window_hours * 3600
    live = [t for t in threads if 0 <= (inp.sent_at - t.last_activity).total_seconds() <= window_s]
    for t in live:
        score, why = 0.0, []
        sk = inp.sender_key
        if sk and sk in t.acked_by:
            score += w.ack_by_sender
            why.append(f"+{w.ack_by_sender:.2f} sender acknowledged this thread earlier")
        if sk and sk in t.assigned_to:
            score += w.assigned_to_sender
            why.append(f"+{w.assigned_to_sender:.2f} sender was @-assigned on this thread")
        compat = status_compat(t, inp)
        if compat:
            add = w.status_compat * compat
            score += add
            why.append(
                f"+{add:.2f} status fit ({inp.kind}/{inp.polarity or '-'} vs {t.last_state})"
            )
        minutes = (inp.sent_at - t.last_activity).total_seconds() / 60
        rec = 0.5 ** (minutes / w.recency_half_life_min)
        score += w.recency * rec
        why.append(f"+{w.recency * rec:.2f} recency ({minutes:.0f} min ago)")
        hit = _attachment_hit(inp.attachment_text, t.identifiers)
        if hit:
            score += w.attachment_match
            why.append(f"+{w.attachment_match:.2f} attachment evidence: '{hit}'")
        cands.append(Candidate(t.key, t.entity_id, t.label, score, why))
    # penalty: already replied substantively here while another candidate is still unanswered
    for c, t in zip(cands, live, strict=True):
        sk = inp.sender_key
        if (
            sk
            and sk in t.substantive_by
            and any(sk not in o.substantive_by for o in live if o is not t)
        ):
            c.score += w.already_replied_penalty
            c.reasons.append(
                f"{w.already_replied_penalty:.2f} sender already replied here; others unanswered"
            )
    for c in cands:
        c.score = round(c.score, 3)
    return sorted(cands, key=lambda c: (-c.score, c.key))


def _attachment_hit(texts: list[str], identifiers: set[str]) -> str | None:
    blob = " ".join(texts).casefold()
    if not blob:
        return None
    for ident in sorted(identifiers, key=len, reverse=True):
        if len(ident) >= 3 and ident.casefold() in blob:
            return ident
    return None


def decide(ranked: list[Candidate], w: LinkWeights) -> Decision:
    if not ranked:
        return Decision("none", [])
    top = ranked[0].score
    second = ranked[1].score if len(ranked) > 1 else 0.0
    if top >= w.auto_link_min_score and top - second >= w.auto_link_min_margin:
        return Decision("auto", ranked)
    return Decision("review", ranked)


def link_message(inp: LinkInput, threads: list[Thread], w: LinkWeights) -> Decision:
    return decide(score_threads(inp, threads, w), w)


def evaluate_links(cases: list[tuple[Decision, str | None]]) -> dict[str, float]:
    """Precision/recall of AUTO-links (build brief 7.3 asks for these in PROGRESS.md).
    cases: (decision, true thread key or None). Wrong auto-links are the thing to drive to zero."""
    autos = [(d, t) for d, t in cases if d.kind == "auto"]
    correct = sum(1 for d, t in autos if d.top and d.top.key == t)
    labelled = [(d, t) for d, t in cases if t is not None]
    review_first = sum(1 for d, t in labelled if d.kind == "review" and d.top and d.top.key == t)
    return {
        "auto_links": len(autos),
        "wrong_auto_links": len(autos) - correct,
        "auto_precision": (correct / len(autos)) if autos else 1.0,
        "auto_recall": (correct / len(labelled)) if labelled else 1.0,
        "review_correct_first": review_first,
        "total": len(cases),
    }
