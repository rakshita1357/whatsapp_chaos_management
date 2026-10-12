"""Conflicts between sources (build brief 8.7). Never overwrite: the winner is recorded in
entity_state, the disagreement becomes a review item.

  stale_source        the workbook said something older than contradicting chat evidence
  contradiction       two sources (or one source on one day) disagree
  ready_but_installed the sheet lists the site 'ready to deploy' while it is installed (INC-50)"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Entity, EntityState, Observation
from app.review.items import raise_review
from app.state.derive import (
    DEFAULT_ORDER,
    claim_class,
    is_installed,
    load_precedence,
)


def sync_conflicts(session: Session, entity_id: uuid.UUID) -> list[str]:
    """Raise the review items this entity's state calls for and retire the ones that no longer
    apply (a conflict that new evidence removed must not linger). Returns the kinds raised."""
    ent = session.get(Entity, entity_id)
    label = (ent.display_name if ent else None) or str(entity_id)
    states = {
        s.dimension: s
        for s in session.scalars(select(EntityState).where(EntityState.entity_id == entity_id))
    }
    prec = load_precedence()
    wanted: dict[str, tuple[str, dict[str, object]]] = {}
    for dim, st in states.items():
        if not st.conflict_flag:
            continue
        obs = list(
            session.scalars(
                select(Observation).where(
                    Observation.entity_id == entity_id, Observation.dimension == dim
                )
            )
        )
        latest: dict[str, Observation] = {}
        for o in obs:
            if o.source_kind not in latest or o.observed_at > latest[o.source_kind].observed_at:
                latest[o.source_kind] = o
        wb, msg = latest.get("workbook_cell"), latest.get("message")
        stale = (
            wb is not None
            and msg is not None
            and claim_class(dim, wb) != claim_class(dim, msg)
            and wb.observed_at < msg.observed_at
        )
        kind = "stale_source" if stale else "contradiction"
        wanted[f"{kind}:{dim}"] = (
            kind,
            {
                "dimension": dim,
                "winner_value": st.value,
                "winner_source": (prec.get(dim) or DEFAULT_ORDER)[0],
                "observations": [
                    {
                        "source": k,
                        "value": o.value,
                        "observed_at": o.observed_at.isoformat(),
                        "id": str(o.id),
                    }
                    for k, o in sorted(latest.items())
                ],
            },
        )
    readiness, install = states.get("readiness"), states.get("installation")
    if readiness and install and readiness.value == "ready" and is_installed(install.value):
        wanted["ready_but_installed:installation"] = (
            "ready_but_installed",
            {"readiness": readiness.value, "installation": install.value},
        )
    raised: list[str] = []
    for key, (kind, detail) in wanted.items():
        raise_review(
            session, kind, dedupe_key=f"conflict:{entity_id}:{key}",
            subject_refs={"entity_id": str(entity_id), "label": label}, proposed_value=detail,
            note=f"{kind.replace('_', ' ')} for {label}",
        )  # fmt: skip
        raised.append(kind)
    # retire open items for conflicts that are gone
    from app.db.models import ReviewItem

    stale_items = session.scalars(
        select(ReviewItem).where(
            ReviewItem.dedupe_key.like(f"conflict:{entity_id}:%"), ReviewItem.status == "open"
        )
    )
    for item in stale_items:
        if (item.dedupe_key or "").removeprefix(f"conflict:{entity_id}:") not in wanted:
            session.delete(item)
    session.flush()
    return raised
