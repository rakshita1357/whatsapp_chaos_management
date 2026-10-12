"""Helpers for pipeline tests: a scripted model double that obeys the real JSON schema."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Client, Entity, Person, PersonAlias
from app.entities.registry import add_identifier, ensure_clients, upsert_entity
from app.providers.base import ProviderError, Usage
from app.workbooks.normalize import division_key

TATA_GROUP = "Tata Delight Meter Core Group"
MARUTI_GROUP = "Maruti Dealers DSS Sales"
RAVI = "+91 90000 00001"
ASHA = "+91 90000 00002"
SAM = "+91 90000 00003"


def cls(
    text: str,
    intent: str = "STATUS_UPDATE",
    *,
    status: list[tuple[str, str, str, str]]
    | None = None,  # (dimension, polarity, value, substring)
    issues: list[tuple[str, str, str | None, str]] | None = None,  # (type, detail, eta, substring)
    mentions: list[tuple[str, str]] | None = None,
    claims: list[tuple[str, str, int, str]] | None = None,  # (metric, scope, value, qualifier)
    conf: float = 0.9,
    lang: str = "en",
    jobs: list[str] | None = None,
) -> dict[str, Any]:
    def span(sub: str) -> list[int]:
        i = text.index(sub)
        return [i, i + len(sub)]

    return {
        "intent": intent, "language": lang,
        "entity_mentions": [{"raw": r, "kind": k} for r, k in (mentions or [])],
        "status_assertions": [
            {"dimension": d, "value": v, "polarity": p, "span": span(sub)} for d, p, v, sub in (status or [])
        ],
        "issue_assertions": [
            {"type": t, "detail": dt, "eta_text": eta, "span": span(sub)} for t, dt, eta, sub in (issues or [])
        ],
        "job_types": jobs or [],
        "aggregate_claims": [
            {"metric": m, "scope": sc, "value": val, "qualifier": q, "note": None} for m, sc, val, q in (claims or [])
        ],
        "refers_to_earlier_message": False, "confidence": conf,
    }  # fmt: skip


class ScriptedProvider:
    """Looks the message text up in a script. Unknown text raises, like a missing cassette."""

    def __init__(self, script: dict[str, dict[str, Any]]) -> None:
        self.script, self.calls = script, 0

    def complete_json(
        self,
        task: str,
        schema: dict[str, Any],
        inputs: dict[str, Any],
        images: list[bytes] | None = None,
    ) -> tuple[dict[str, Any], Usage, str]:
        text = json.loads(inputs["prompt"])["message"]["text"]
        self.calls += 1
        if text not in self.script:
            raise ProviderError(f"script has no entry for {text!r}")
        return self.script[text], Usage(100, 40), "scripted"


def seed_people(session: Session) -> dict[str, Person]:
    out: dict[str, Person] = {}
    for key, name in (("919000000001", "Ravi"), ("919000000002", "Asha"), ("919000000003", "Sam")):
        p = Person(sender_key=key, display_label=name, name=name, source="confirmed")
        session.add(p)
        session.flush()
        session.add(PersonAlias(person_id=p.id, alias=name))
        out[name] = p
    session.flush()
    return out


def tata_client(session: Session) -> Client:
    return ensure_clients(session)["tata"]


def seed_site(
    session: Session, dealer: str, division: str, city: str, client: Client | None = None
) -> Entity:
    client = client or tata_client(session)
    d, _ = upsert_entity(session, "dealer_code", client.id, dealer, display_name=dealer)
    key = division_key(division)
    e, _ = upsert_entity(
        session, "division", client.id, key, display_name=division, parent_id=d.id,
        attributes={"city": city},
    )  # fmt: skip
    add_identifier(session, e.id, "division_code", division, key, "test")
    return e


def native(group_lines: list[tuple[str, str, str]]) -> str:
    """[(HH:MM, sender label, text)] on 09/10/2026 (IST), as an Android export."""
    out = []
    for hm, sender, text in group_lines:
        hh, mm = map(int, hm.split(":"))
        ap = "pm" if hh >= 12 else "am"
        out.append(f"09/10/2026, {hh % 12 or 12}:{mm:02d} {ap} - {sender}: {text}")
    return "\n".join(out) + "\n"


def new_id() -> uuid.UUID:
    return uuid.uuid4()


__all__ = ["division_key"]
