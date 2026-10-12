"""Entity resolution (build brief 7.1, 8.5). Tata: dealer code owns divisions, the division is the
site. Maruti: no codes in chat; an outlet = company + channel + town. Ambiguity goes to review."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Entity, EntityIdentifier
from app.entities.registry import add_identifier, upsert_entity
from app.workbooks.normalize import division_key

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def norm_name(s: str) -> str:
    return _NON_ALNUM.sub(" ", s.casefold()).strip()


@dataclass
class Resolution:
    entity_id: uuid.UUID | None
    method: str  # division_code | dealer_code_unique | dealer_code_town | company_town | none
    confidence: float
    candidates: list[dict[str, Any]] = field(default_factory=list)
    ambiguity: str | None = None  # division_ambiguity | entity_match_ambiguous
    created: bool = False


NONE = Resolution(None, "none", 0.0)


def resolve_tata(
    session: Session,
    client_id: uuid.UUID | None,
    division_codes: list[str],
    dealer_codes: list[str],
    towns: list[str],
) -> Resolution:
    """Exact division code wins; a dealer code resolves only if it has one division, or a town
    mention singles one out. Anything else is a review item, never a guess."""

    def q() -> Any:
        s = select(Entity)
        return (
            s.where(Entity.client_id == client_id)
            if client_id
            else s.where(Entity.client_id.is_(None))
        )

    for code in division_codes:
        key = division_key(code)
        e = session.scalar(q().where(Entity.type == "division", Entity.canonical_key == key))
        if e is None:
            hit = session.scalar(
                select(Entity)
                .join(EntityIdentifier, EntityIdentifier.entity_id == Entity.id)
                .where(Entity.type == "division", EntityIdentifier.normalized_value == key)
            )
            e = hit
        if e is not None:
            return Resolution(e.id, "division_code", 1.0)
        return Resolution(
            None, "none", 0.0, [{"raw": code}], ambiguity="entity_match_ambiguous"
        )  # a code we do not know: review, do not invent a site
    for code in dealer_codes:
        dealer = session.scalar(
            q().where(Entity.type == "dealer_code", Entity.canonical_key == code)
        )
        if dealer is None:
            continue
        kids = list(session.scalars(select(Entity).where(Entity.parent_id == dealer.id)))
        if len(kids) == 1:
            return Resolution(kids[0].id, "dealer_code_unique", 0.95)
        if len(kids) > 1:
            cands = [
                {
                    "entity_id": str(k.id),
                    "label": k.display_name,
                    "city": (k.attributes or {}).get("city"),
                }
                for k in kids
            ]
            picked = _pick_by_town(kids, towns)
            if picked is not None:
                return Resolution(picked.id, "dealer_code_town", 0.9, cands)
            return Resolution(None, "none", 0.0, cands, ambiguity="division_ambiguity")
    return NONE


def _pick_by_town(kids: list[Entity], towns: list[str]) -> Entity | None:
    wanted = [norm_name(t) for t in towns if t]
    if not wanted:
        return None
    scored: list[tuple[float, Entity]] = []
    for k in kids:
        city = norm_name(str((k.attributes or {}).get("city") or ""))
        label = norm_name(k.display_name or "")
        best = max(
            (max(fuzz.ratio(t, city), 100 if t and t in label.split() else 0) for t in wanted),
            default=0,
        )
        scored.append((best, k))
    scored.sort(key=lambda x: -x[0])
    if scored and scored[0][0] >= 88 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 10):
        return scored[0][1]
    return None


def outlet_key(company: str, channel: str | None, town: str) -> str:
    return "|".join([norm_name(company), (channel or "").casefold(), norm_name(town)])


def resolve_maruti(
    session: Session,
    client_id: uuid.UUID | None,
    company: str | None,
    channel: str | None,
    town: str | None,
    create: bool = True,
) -> Resolution:
    """Outlet = company + channel (ARENA/NEXA) + town. Needs company AND town; the outlet is created
    provisionally (no master list exists for Maruti) so history can accumulate and be verified."""
    if not company or not town:
        return NONE
    ch = channel.upper() if channel and channel.upper() in ("ARENA", "NEXA") else None
    key = outlet_key(company, ch, town)
    e = session.scalar(
        select(Entity).where(
            Entity.type == "outlet",
            Entity.canonical_key == key,
            Entity.client_id == client_id if client_id else Entity.client_id.is_(None),
        )
    )
    if e is not None:
        return Resolution(e.id, "company_town", 0.85 if ch else 0.7)
    if not create:
        return NONE
    e, created = upsert_entity(
        session,
        "outlet",
        client_id,
        key,
        display_name=" ".join(x for x in (company, ch, town) if x),
        attributes={"company": company, "channel": ch, "town": town},
        provisional=True,
    )
    add_identifier(session, e.id, "name_alias", key, key, "chat")
    return Resolution(e.id, "company_town", 0.85 if ch else 0.7, created=created)
