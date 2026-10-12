"""Model classification of one message or bundle (build brief 7.2). The model returns a PROPOSAL;
`validate_classification` enforces the invariants:
  * acknowledgements never change status (INC-51)
  * aggregates never create events
  * every status assertion must be attributable to a span of the message text."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.providers.base import ModelProvider, ProviderError, Usage
from app.understand.rules import RuleHints
from app.understand.settings import UnderstandingConfig

INTENTS = [
    "STATUS_UPDATE", "QUESTION", "UPDATE_REQUEST", "ACKNOWLEDGEMENT", "AGGREGATE_REPORT",
    "ATTACHMENT_WITH_CONTEXT", "DOCUMENT_UPLOAD", "ESCALATION", "POINTER",
    "UNRELATED_OR_UNCLEAR", "CONFLICT_OR_CORRECTION",
]  # fmt: skip
ISSUE_TYPES = [
    "screen_incompatible_with_dmp_box", "no_screen_available", "renovation", "relocation",
    "electricity", "tv_under_repair", "device_misconfigured", "network", "other",
]  # fmt: skip
MENTION_KINDS = [
    "division_code",
    "dealer_code",
    "company",
    "town",
    "channel",
    "sol_id",
    "phone",
    "pin",
]
STATUS_CREATING_INTENTS = {
    "STATUS_UPDATE", "ATTACHMENT_WITH_CONTEXT", "ESCALATION", "CONFLICT_OR_CORRECTION",
    "POINTER", "UPDATE_REQUEST",
}  # fmt: skip

Intent = Literal[
    "STATUS_UPDATE", "QUESTION", "UPDATE_REQUEST", "ACKNOWLEDGEMENT", "AGGREGATE_REPORT",
    "ATTACHMENT_WITH_CONTEXT", "DOCUMENT_UPLOAD", "ESCALATION", "POINTER",
    "UNRELATED_OR_UNCLEAR", "CONFLICT_OR_CORRECTION",
]  # fmt: skip


class EntityMention(BaseModel):
    raw: str
    kind: Literal[
        "division_code", "dealer_code", "company", "town", "channel", "sol_id", "phone", "pin"
    ]


class StatusAssertion(BaseModel):
    dimension: Literal["installation", "activation", "readiness"]
    value: str
    polarity: Literal["positive", "negative"]
    span: list[int]


class IssueAssertion(BaseModel):
    type: Literal[
        "screen_incompatible_with_dmp_box", "no_screen_available", "renovation", "relocation",
        "electricity", "tv_under_repair", "device_misconfigured", "network", "other",
    ]  # fmt: skip
    detail: str
    eta_text: str | None = None
    span: list[int]


class AggregateClaimOut(BaseModel):
    metric: str
    scope: Literal["yesterday", "today", "program"]
    value: int
    qualifier: Literal["=", ">", "~"] = "="
    note: str | None = None


class Classification(BaseModel):
    intent: Intent
    language: Literal["en", "hi", "hinglish", "other"] = "en"
    entity_mentions: list[EntityMention] = Field(default_factory=list)
    status_assertions: list[StatusAssertion] = Field(default_factory=list)
    issue_assertions: list[IssueAssertion] = Field(default_factory=list)
    job_types: list[str] = Field(default_factory=list)
    aggregate_claims: list[AggregateClaimOut] = Field(default_factory=list)
    refers_to_earlier_message: bool = False
    confidence: float = 0.0


def _obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": required if required is not None else list(props),
        "additionalProperties": False,
    }


_SPAN = {"type": "array", "items": {"type": "integer"}}
CLASSIFICATION_SCHEMA: dict[str, Any] = _obj(
    {
        "intent": {"type": "string", "enum": INTENTS},
        "language": {"type": "string", "enum": ["en", "hi", "hinglish", "other"]},
        "entity_mentions": {
            "type": "array",
            "items": _obj(
                {"raw": {"type": "string"}, "kind": {"type": "string", "enum": MENTION_KINDS}}
            ),
        },
        "status_assertions": {
            "type": "array",
            "items": _obj(
                {
                    "dimension": {
                        "type": "string",
                        "enum": ["installation", "activation", "readiness"],
                    },
                    "value": {"type": "string"},
                    "polarity": {"type": "string", "enum": ["positive", "negative"]},
                    "span": _SPAN,
                }
            ),
        },
        "issue_assertions": {
            "type": "array",
            "items": _obj(
                {
                    "type": {"type": "string", "enum": ISSUE_TYPES},
                    "detail": {"type": "string"},
                    "eta_text": {"type": ["string", "null"]},
                    "span": _SPAN,
                }
            ),
        },
        "job_types": {"type": "array", "items": {"type": "string"}},
        "aggregate_claims": {
            "type": "array",
            "items": _obj(
                {
                    "metric": {"type": "string"},
                    "scope": {"type": "string", "enum": ["yesterday", "today", "program"]},
                    "value": {"type": "integer"},
                    "qualifier": {"type": "string", "enum": ["=", ">", "~"]},
                    "note": {"type": ["string", "null"]},
                }
            ),
        },
        "refers_to_earlier_message": {"type": "boolean"},
        "confidence": {"type": "number"},
    }
)

SYSTEM_PROMPT = """You read WhatsApp messages from an operations group that installs and activates \
TV/DMP devices at car-dealer sites for a retail client. Messages are English, Hindi or Hinglish.
Return ONLY the JSON object required by the schema. Your output is a proposal that code will validate.

Rules:
- intent: STATUS_UPDATE (reports installed / not installed / activated / ready), QUESTION, UPDATE_REQUEST \
(asks someone to act or to report status), ACKNOWLEDGEMENT (ok / noted / checking / thumbs-up only), \
AGGREGATE_REPORT (a count for the whole programme or a day, e.g. "yesterday 15"), \
ATTACHMENT_WITH_CONTEXT (media with a short caption), DOCUMENT_UPLOAD, ESCALATION, \
POINTER (a bare pointing-hand emoji referring to the message above), UNRELATED_OR_UNCLEAR, \
CONFLICT_OR_CORRECTION (corrects an earlier claim).
- An acknowledgement NEVER changes a status. Never invent a status from an acknowledgement.
- status_assertions: only what the text itself states. `span` = [start, end) character offsets into \
message.text covering the words that state it. If you cannot point at words, do not assert.
- polarity: positive = installed/activated/ready; negative = not installed / not activated / not ready.
- issue_assertions: blockers (renovation, relocation, electricity, no screen, screen incompatible with \
the DMP box, TV under repair, device misconfigured, network, other) with the exact eta wording if given \
(e.g. "2-3 days"), else null.
- aggregate_claims: totals such as "yesterday 15" or "more than 100". These are group-level claims, \
not site events; do not also emit status_assertions for them.
- entity_mentions: codes, company, town, channel (ARENA/NEXA), Sol ID, phone or PIN exactly as written.
- Use rule_hints as hints only; confirm or ignore them. Never use information outside the message and \
the provided context. If unsure, lower confidence.

Examples (message -> key fields):
"TV laga diya hai" (Hinglish) -> STATUS_UPDATE, installation positive, span covers "laga diya".
"abhi tak install nahi hua" -> STATUS_UPDATE, installation negative, language hinglish.
"bijli ki problem hai, 2-3 din lagenge" -> issue electricity, eta_text "2-3 din", language hinglish.
"kal 12 installation hue" -> AGGREGATE_REPORT, aggregate yesterday=12, no status_assertions.
"Ok checking" -> ACKNOWLEDGEMENT, no assertions.
"Yesterday - 15, today - 9 so far" -> AGGREGATE_REPORT, yesterday 15 and today 9 (partial).
"renovation going on, will do after 10 days" -> issue renovation, eta_text "after 10 days".
"3007530-Sv&Pa-Chokidhani-RdrMtr NO TV INSTALLED" -> STATUS_UPDATE, installation negative.
"""


@dataclass
class ClassifyContext:
    sender: str | None
    sent_at_ist: str
    text: str
    attachment_kinds: list[str] = field(default_factory=list)
    previous: list[dict[str, str]] = field(default_factory=list)
    quoted: str | None = None


def build_inputs(ctx: ClassifyContext, hints: RuleHints) -> dict[str, Any]:
    """Deterministic (no clocks or random ids) so cassette keys are stable."""
    import json

    body = {
        "message": {
            "sender": ctx.sender,
            "sent_at_ist": ctx.sent_at_ist,
            "text": ctx.text,
            "attachment_kinds": ctx.attachment_kinds,
            "quoted": ctx.quoted,
        },
        "previous_messages": ctx.previous,
        "rule_hints": hints.as_prompt_json(),
    }
    return {"system": SYSTEM_PROMPT, "prompt": json.dumps(body, ensure_ascii=False, sort_keys=True)}


@dataclass
class ValidatedClassification:
    c: Classification
    issues: list[str] = field(default_factory=list)
    creates_status_events: bool = True
    low_confidence: bool = False
    usage: Usage = field(default_factory=Usage)
    model_id: str = ""


class ClassificationInvalid(ProviderError):
    pass


def validate_classification(
    raw: dict[str, Any], text: str, hints: RuleHints, cfg: UnderstandingConfig
) -> ValidatedClassification:
    try:
        c = Classification.model_validate(raw)
    except ValidationError as exc:
        raise ClassificationInvalid(
            f"classification failed schema validation: {exc.error_count()} errors"
        ) from exc
    v = ValidatedClassification(c)
    # rules are authoritative for acknowledgements and bare pointers (INC-51)
    if hints.is_ack and c.intent != "ACKNOWLEDGEMENT":
        v.issues.append(f"intent {c.intent} overridden to ACKNOWLEDGEMENT by rules")
        c.intent = "ACKNOWLEDGEMENT"
    if c.intent == "ACKNOWLEDGEMENT":
        if c.status_assertions or c.issue_assertions or c.aggregate_claims:
            v.issues.append("acknowledgement carried assertions: dropped")
        c.status_assertions, c.issue_assertions, c.aggregate_claims = [], [], []
    # every status/issue must be attributable to words in the message
    kept_s = []
    for s in c.status_assertions:
        if _span_ok(s.span, text):
            kept_s.append(s)
        else:
            v.issues.append(f"unattributable status assertion dropped: {s.dimension}={s.value}")
    c.status_assertions = kept_s
    kept_i = []
    for i in c.issue_assertions:
        if _span_ok(i.span, text):
            kept_i.append(i)
        else:
            v.issues.append(f"unattributable issue assertion dropped: {i.type}")
    c.issue_assertions = kept_i
    if c.intent == "AGGREGATE_REPORT" or c.aggregate_claims:
        v.creates_status_events = False  # aggregates never create events
        if c.intent == "AGGREGATE_REPORT" and (c.status_assertions or c.issue_assertions):
            v.issues.append("aggregate report carried per-site assertions: ignored")
            c.status_assertions, c.issue_assertions = [], []
    if c.intent in ("QUESTION", "UNRELATED_OR_UNCLEAR") and (
        c.status_assertions or c.issue_assertions
    ):
        v.issues.append(f"{c.intent} carried assertions: dropped")
        c.status_assertions, c.issue_assertions = [], []
    if c.intent == "POINTER" and not text.strip(
        " ☝\U0001f446️\U0001f3fb\U0001f3fc\U0001f3fd\U0001f3fe\U0001f3ff"
    ):
        c.status_assertions, c.issue_assertions = [], []  # a bare pointer states nothing
    if not 0.0 <= c.confidence <= 1.0:
        c.confidence = max(0.0, min(1.0, c.confidence))
    v.low_confidence = c.confidence < cfg.min_confidence
    return v


def _span_ok(span: list[int], text: str) -> bool:
    if len(span) != 2:
        return False
    a, b = span
    return 0 <= a < b <= len(text) and bool(text[a:b].strip())


def classify(
    provider: ModelProvider,
    ctx: ClassifyContext,
    hints: RuleHints,
    cfg: UnderstandingConfig,
) -> ValidatedClassification:
    inputs = build_inputs(ctx, hints)
    raw, usage, model_id = provider.complete_json("classify_message", CLASSIFICATION_SCHEMA, inputs)
    v = validate_classification(raw, ctx.text, hints, cfg)
    v.usage, v.model_id = usage, model_id
    return v
