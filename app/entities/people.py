"""People registry: seed from config/people.yaml and resolve display-name labels to phone keys."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import load_yaml
from app.db.models import Person, PersonAlias
from app.ingest.envelope import MessageEnvelope
from app.ingest.text import normalize_text, sender_key_for


def seed_people(session: Session, data: dict[str, Any] | None = None) -> int:
    data = data if data is not None else (load_yaml("people") or {})
    n = 0
    for p in data.get("people") or []:
        key = str(p["sender_key"]) if p.get("sender_key") else None
        person = session.scalar(select(Person).where(Person.sender_key == key)) if key else None
        if person is None:
            person = Person(
                sender_key=key,
                display_label=p.get("label") or p.get("name") or key or "",
                name=p.get("name"),
                team=p.get("team"),
                source="confirmed" if key else "display_name",
            )
            session.add(person)
            session.flush()
            n += 1
        existing = set(
            session.scalars(select(PersonAlias.alias).where(PersonAlias.person_id == person.id))
        )
        for alias in [p.get("name"), p.get("label"), *(p.get("aliases") or [])]:
            a = normalize_text(alias) if alias else None
            if a and a.casefold() not in {x.casefold() for x in existing}:
                session.add(PersonAlias(person_id=person.id, alias=a))
                existing.add(a)
    session.flush()
    return n


def resolve_sender_keys(session: Session, envs: list[MessageEnvelope]) -> int:
    """Name-only labels that match a known person alias take that person's phone key."""
    cache: dict[str, str | None] = {}
    changed = 0
    for e in envs:
        if not e.sender_label or (e.sender_key or "").isdigit():
            continue
        label = (normalize_text(e.sender_label) or "").casefold()
        if label not in cache:
            row = session.execute(
                select(Person.sender_key)
                .join(PersonAlias, PersonAlias.person_id == Person.id)
                .where(func_lower(PersonAlias.alias) == label, Person.sender_key.is_not(None))
            ).first()
            cache[label] = row[0] if row else None
        key = cache[label]
        if key and key != e.sender_key:
            e.sender_key = key
            changed += 1
    return changed


def func_lower(col: Any) -> Any:
    from sqlalchemy import func

    return func.lower(col)


__all__ = ["seed_people", "resolve_sender_keys", "sender_key_for"]
