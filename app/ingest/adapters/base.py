from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.ingest.envelope import MessageEnvelope


@dataclass
class ParseResult:
    format: str
    envelopes: list[MessageEnvelope]
    group_name: str | None = None
    date_order: str | None = None
    date_order_evidence: dict[str, Any] = field(default_factory=dict)
    needs_review: bool = False
    warnings: list[str] = field(default_factory=list)


class ExportParser(Protocol):
    def parse(self, text: str, group_name: str | None = None) -> ParseResult: ...
