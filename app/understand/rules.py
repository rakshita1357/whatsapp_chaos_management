"""Deterministic first pass (build brief 7.2). Rules give hints; the model confirms or corrects.
Acknowledgements are decided here and can never change a status (INC-51)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.config import load_yaml

POINTER_RE = re.compile("[☝\U0001f446][\U0001f3fb-\U0001f3ff️]*")
_ACK_TOKENS = {"ok", "okay", "noted", "will", "check", "checking", "sir", "ji", "\U0001f44d"}
_ACK_CORE = {"ok", "okay", "noted", "check", "checking", "\U0001f44d"}
_SKIN = re.compile("[\U0001f3fb-\U0001f3ff️]")

STATUS_KEYWORDS = [
    "no tv installed", "not installed", "installed", "location ready", "not supporting",
    "activated", "activation", "renovation", "relocat\\w*", "electricity", "repair",
    "bodyshop", "ready", "live",
    # Hinglish hints (the model still decides)
    "laga diya", "lag gaya", "install nahi", "nahi hua", "bijli",
]  # fmt: skip
_KW_RE = re.compile(r"(?<!\w)(" + "|".join(STATUS_KEYWORDS) + r")(?!\w)", re.IGNORECASE)

_ADDRESS_WORDS = re.compile(
    r"pin|address|road|nagar|near|opp|floor|colony|sector|street|dist|city|plot|lane",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r"(?<![\d])(?:\+?91[\s-]?)?([6-9]\d{4}[\s-]?\d{5})(?!\d)")
_PIN_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_SOL_RE = re.compile(r"Sol\s*ID\s*[:\-]?\s*([0-9A-Z]{3,6})", re.IGNORECASE)
_SERIAL_RE = re.compile(
    r"(?<![0-9A-Za-z])(?=[0-9A-Z]*\d)(?=[0-9A-Z]*[A-Z])[0-9A-Z]{8,16}(?![0-9A-Za-z])"
)
_MENTION_RES = [
    ("name", re.compile("@⁨([^⁩]+)⁩")),
    ("name", re.compile(r"@~\s?([^\s@][^@\n]*?)(?=\s{2,}|$|\n|@)")),
    ("phone", re.compile(r"@(\+?\d[\d\s]{8,}\d)")),
]
_AGG_RES: list[tuple[str, str, re.Pattern[str]]] = [
    ("yesterday", "=", re.compile(r"yesterday[-:\s]+(\d+)", re.IGNORECASE)),
    ("today", "=", re.compile(r"today[-:\s]+.{0,40}?(\d+)", re.IGNORECASE)),
    ("program", ">", re.compile(r"more than (\d+)", re.IGNORECASE)),
    ("yesterday", "=", re.compile(r"\bkal\s+(\d+)\b", re.IGNORECASE)),  # Hinglish: kal 12 ...
]


@dataclass
class Hit:
    value: str
    span: tuple[int, int]


@dataclass
class AggregateCandidate:
    scope: str
    qualifier: str
    value: int
    span: tuple[int, int]


@dataclass
class RuleHints:
    division_codes: list[Hit] = field(default_factory=list)
    dealer_codes: list[Hit] = field(default_factory=list)
    sol_ids: list[Hit] = field(default_factory=list)
    serials: list[Hit] = field(default_factory=list)
    phones: list[Hit] = field(default_factory=list)
    pins: list[Hit] = field(default_factory=list)
    keywords: list[Hit] = field(default_factory=list)
    mentions: list[Hit] = field(default_factory=list)  # value = rendering, kind in `mention_kinds`
    mention_kinds: list[str] = field(default_factory=list)
    aggregates: list[AggregateCandidate] = field(default_factory=list)
    is_ack: bool = False
    is_pointer: bool = False

    def as_prompt_json(self) -> dict[str, Any]:
        def v(hits: list[Hit]) -> list[str]:
            return [h.value for h in hits]

        return {
            "division_codes": v(self.division_codes),
            "dealer_codes": v(self.dealer_codes),
            "sol_ids": v(self.sol_ids),
            "serials": v(self.serials),
            "phones": v(self.phones),
            "pins": v(self.pins),
            "status_keywords": v(self.keywords),
            "mentions": v(self.mentions),
            "aggregate_candidates": [
                {"scope": a.scope, "qualifier": a.qualifier, "value": a.value}
                for a in self.aggregates
            ],
            "looks_like_acknowledgement": self.is_ack,
            "looks_like_pointer": self.is_pointer,
        }


def _unanchor(rx: str) -> str:
    return rx.removeprefix("^").removesuffix("$")


class RuleEngine:
    """Code patterns come from config/groups.yaml, never hard-coded here."""

    def __init__(self, groups_cfg: dict[str, Any] | None = None) -> None:
        cfg = groups_cfg if groups_cfg is not None else (load_yaml("groups") or {})
        tata = (cfg.get("clients") or {}).get("tata", {})
        dealer = _unanchor(tata.get("dealer_code_regex", r"30\d[0-9A-Z]\d{3}"))
        division = _unanchor(tata.get("division_code_regex", r"30\d[0-9A-Z]\d{3}-.+"))
        division = re.sub(r"\.\+$", r"[^\\s]+", division)
        self.division_re = re.compile(r"(?<![\w-])(" + division + r")")
        self.dealer_re = re.compile(r"(?<![\w-])(" + dealer + r")(?![\w]|-\S)")

    def analyse(self, normalized_text: str | None, raw_text: str | None = None) -> RuleHints:
        text = normalized_text or ""
        h = RuleHints()
        for m in self.division_re.finditer(text):
            h.division_codes.append(Hit(m.group(1), m.span(1)))
        for m in self.dealer_re.finditer(text):
            h.dealer_codes.append(Hit(m.group(1), m.span(1)))
        for m in _SOL_RE.finditer(text):
            h.sol_ids.append(Hit(m.group(1).upper(), m.span(1)))
        taken = [x.span for x in h.division_codes + h.dealer_codes]
        for m in _SERIAL_RE.finditer(text):
            if not any(a <= m.start() < b for a, b in taken):
                h.serials.append(Hit(m.group(0), m.span()))
        for m in _PHONE_RE.finditer(text):
            h.phones.append(Hit(re.sub(r"\D", "", m.group(1)), m.span(1)))
        phone_spans = [x.span for x in h.phones]
        for m in _PIN_RE.finditer(text):
            window = text[max(0, m.start() - 40) : m.end() + 40]
            inside_phone = any(a <= m.start() < b for a, b in phone_spans)
            if not inside_phone and _ADDRESS_WORDS.search(window):
                h.pins.append(Hit(m.group(1), m.span(1)))
        for m in _KW_RE.finditer(text):
            h.keywords.append(Hit(m.group(1).casefold(), m.span(1)))
        src = raw_text if raw_text is not None else text
        for kind, rx in _MENTION_RES:
            for m in rx.finditer(src):
                h.mentions.append(Hit(m.group(1).strip(), m.span(1)))
                h.mention_kinds.append(kind)
        for scope, qual, rx in _AGG_RES:
            for m in rx.finditer(text):
                h.aggregates.append(AggregateCandidate(scope, qual, int(m.group(1)), m.span(1)))
        h.is_ack = is_acknowledgement(text)
        h.is_pointer = is_pointer(text)
        return h


def _tokens(text: str) -> list[str]:
    t = _SKIN.sub("", text.casefold())
    t = t.replace("\U0001f44d", " \U0001f44d ")
    t = re.sub(r"[.,!?;:\-_~*]+", " ", t)
    return [x for x in t.split() if x]


def is_acknowledgement(text: str | None) -> bool:
    """`ok`, `noted`, `will check`, `ok checking`, thumbs-up... Never a status change."""
    toks = _tokens(text or "")
    return bool(toks) and all(t in _ACK_TOKENS for t in toks) and any(t in _ACK_CORE for t in toks)


def is_pointer(text: str | None) -> bool:
    """A bare ☝️ / 👆, optionally with one short word like LIVE, pointing at the thing above."""
    t = (text or "").strip()
    if not POINTER_RE.search(t):
        return False
    rest = POINTER_RE.sub("", t).strip(" .!")
    return len(rest.split()) <= 2 and len(rest) <= 20


__all__ = [
    "RuleEngine",
    "RuleHints",
    "Hit",
    "AggregateCandidate",
    "is_acknowledgement",
    "is_pointer",
]
