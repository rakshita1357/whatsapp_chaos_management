"""Deterministic derived state (build brief 10). `derive_entity` rebuilds `entity_state` for one
entity purely from its observations (and open issue events). Same input, same output.

Installation ladder:
  not_reported < planned < pending_install < not_installed_reported < installation_reported
  < installed_with_evidence < verified
D6: "installed" for counting = installation_reported or above. "verified" needs a human review
action; a form + photo never auto-verify (D08 is open)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import load_yaml
from app.db.models import EntityState, Event, EventEvidence, Observation

LADDER = [
    "not_reported", "planned", "pending_install", "not_installed_reported",
    "installation_reported", "installed_with_evidence", "verified",
]  # fmt: skip
INSTALLED_LEVEL = LADDER.index("installation_reported")
VALUE_TO_LEVEL = {
    "not_reported": 0, "planned": 1, "pending_install": 2, "not_installed_reported": 3,
    "installation_reported": 4, "completed": 4,  # a workbook "Completed" is a claim = reported
    "installed_with_evidence": 5, "verified": 6,
}  # fmt: skip
DEFAULT_ORDER = ["human", "attachment", "message", "workbook_cell"]
POSITIVE_VALUES = {"ready", "done", "activated", "true", "installation_reported", "completed"}


def load_precedence(config_dir: Path | None = None) -> dict[str, list[str]]:
    raw = load_yaml("precedence", config_dir) or {}
    return {k: list(v) for k, v in (raw.get("dimension_precedence") or {}).items()}


def level_of(value: str | None) -> int:
    return VALUE_TO_LEVEL.get(value or "", -1)


def is_installed(value: str | None) -> bool:
    """D6: counts as installed from installation_reported upward."""
    return level_of(value) >= INSTALLED_LEVEL


def claim_class(dimension: str, o: Observation) -> str:
    """Coarse meaning of an observation, used to decide whether two sources disagree."""
    if dimension == "installation":
        lv = level_of(o.value)
        return "yes" if lv >= INSTALLED_LEVEL else "no"
    if o.polarity in ("positive", "negative"):
        return "yes" if o.polarity == "positive" else "no"
    return "yes" if (o.value or "") in POSITIVE_VALUES else "no"


@dataclass
class Derived:
    dimension: str
    value: str | None
    winner: Observation
    considered: list[Observation]
    conflict: bool
    conflict_kinds: list[str]


def _latest(obs: list[Observation]) -> Observation:
    return max(obs, key=lambda o: (o.observed_at, str(o.id)))


def _has_attachment_evidence(session: Session, event_id: uuid.UUID | None) -> bool:
    if event_id is None:
        return False
    return (
        session.scalar(
            select(EventEvidence.id).where(
                EventEvidence.event_id == event_id, EventEvidence.attachment_id.is_not(None)
            )
        )
        is not None
    )


def derive_dimension(
    session: Session, dimension: str, obs: list[Observation], order: list[str]
) -> Derived | None:
    if not obs:
        return None
    by_kind: dict[str, list[Observation]] = {}
    for o in obs:
        by_kind.setdefault(o.source_kind, []).append(o)
    ranked = [k for k in order if k in by_kind] + [k for k in by_kind if k not in order]
    latest = {k: _latest(v) for k, v in by_kind.items()}
    winner = latest[ranked[0]]
    kinds: list[str] = []
    if len({claim_class(dimension, o) for o in latest.values()}) > 1:
        kinds.append("cross_source")
    for v in by_kind.values():  # same source disagreeing with itself on one day
        per_day: dict[object, set[str]] = {}
        for o in v:
            per_day.setdefault(o.effective_date, set()).add(claim_class(dimension, o))
        if any(len(s) > 1 for s in per_day.values()):
            kinds.append("same_source")
            break
    value = winner.value
    if dimension == "installation":
        lv = level_of(winner.value)
        if winner.source_kind == "human" and winner.value == "verified":
            lv = LADDER.index("verified")  # only a human review action verifies
        elif lv == INSTALLED_LEVEL and _has_attachment_evidence(session, winner.event_id):
            lv = LADDER.index("installed_with_evidence")
        value = LADDER[lv] if lv >= 0 else winner.value
    return Derived(dimension, value, winner, list(latest.values()), bool(kinds), kinds)


def derive_entity(
    session: Session, entity_id: uuid.UUID, precedence: dict[str, list[str]] | None = None
) -> list[EntityState]:
    prec = precedence if precedence is not None else load_precedence()
    session.flush()
    obs = list(
        session.scalars(
            select(Observation)
            .where(Observation.entity_id == entity_id)
            .order_by(Observation.observed_at, Observation.id)
        )
    )
    session.execute(delete(EntityState).where(EntityState.entity_id == entity_id))
    by_dim: dict[str, list[Observation]] = {}
    for o in obs:
        if o.dimension != "issue":
            by_dim.setdefault(o.dimension, []).append(o)
    states: list[EntityState] = []
    for dim, items in sorted(by_dim.items()):
        d = derive_dimension(session, dim, items, prec.get(dim, DEFAULT_ORDER))
        if d is None:
            continue
        states.append(
            EntityState(
                entity_id=entity_id,
                dimension=dim,
                value=d.value,
                as_of=d.winner.observed_at,
                derived_from_observation_ids=[
                    str(d.winner.id),
                    *[str(o.id) for o in d.considered if o.id != d.winner.id],
                ],
                conflict_flag=d.conflict,
            )
        )
    open_issues = list(
        session.scalars(
            select(Event)
            .where(
                Event.entity_id == entity_id,
                Event.type == "issue",
                Event.lifecycle_status.in_(
                    ["open", "acknowledged", "investigating", "waiting_external"]
                ),
            )
            .order_by(Event.opened_at)
        )
    )
    if open_issues:
        types = sorted({str(e.payload.get("issue_type", "other")) for e in open_issues})
        states.append(
            EntityState(
                entity_id=entity_id,
                dimension="issue",
                value=",".join(types),
                as_of=max(e.opened_at for e in open_issues if e.opened_at),
                derived_from_observation_ids=[str(e.id) for e in open_issues],
                conflict_flag=False,
            )
        )
    for s in states:
        session.add(s)
    session.flush()
    return states


def derive_entities(session: Session, entity_ids: set[uuid.UUID]) -> int:
    prec = load_precedence()
    for eid in sorted(entity_ids, key=str):
        derive_entity(session, eid, prec)
    return len(entity_ids)
