"""Entity registry helpers: clients/groups seeding and idempotent entity upserts."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import load_yaml
from app.db.models import Client, Entity, EntityIdentifier, Group


def ensure_clients(session: Session, groups_cfg: dict[str, Any] | None = None) -> dict[str, Client]:
    cfg = groups_cfg if groups_cfg is not None else (load_yaml("groups") or {})
    by_key: dict[str, Client] = {}
    for key, spec in (cfg.get("clients") or {}).items():
        c = session.scalar(select(Client).where(Client.key == key))
        if c is None:
            c = Client(key=key, name=spec.get("name", key))
            session.add(c)
            session.flush()
        by_key[key] = c
    return by_key


def seed_clients_and_groups(session: Session, groups_cfg: dict[str, Any] | None = None) -> None:
    cfg = groups_cfg if groups_cfg is not None else (load_yaml("groups") or {})
    by_key = ensure_clients(session, cfg)
    for g in cfg.get("groups") or []:
        client = by_key.get(g.get("client", ""))
        row = session.scalar(select(Group).where(Group.name == g["name"]))
        if row is None:
            session.add(Group(name=g["name"], client_id=client.id if client else None))
        elif row.client_id is None and client:
            row.client_id = client.id
    session.flush()


def client_id_for_group_name(session: Session, name: str) -> uuid.UUID | None:
    """Client configured for a group name in config/groups.yaml (None if not configured)."""
    cfg = load_yaml("groups") or {}
    for g in cfg.get("groups") or []:
        if g.get("name") == name:
            c = ensure_clients(session, cfg).get(g.get("client", ""))
            return c.id if c else None
    return None


def client_by_key(session: Session, key: str) -> Client | None:
    return ensure_clients(session).get(key)


def upsert_entity(
    session: Session,
    type_: str,
    client_id: uuid.UUID | None,
    canonical_key: str,
    *,
    display_name: str | None = None,
    parent_id: uuid.UUID | None = None,
    attributes: dict[str, Any] | None = None,
    provisional: bool = False,
) -> tuple[Entity, bool]:
    q = select(Entity).where(Entity.type == type_, Entity.canonical_key == canonical_key)
    q = q.where(Entity.client_id == client_id) if client_id else q.where(Entity.client_id.is_(None))
    e = session.scalar(q)
    if e is not None:
        merged = {**(e.attributes or {}), **{k: v for k, v in (attributes or {}).items() if v}}
        if merged != e.attributes:
            e.attributes = merged
        if parent_id and not e.parent_id:
            e.parent_id = parent_id
        return e, False
    e = Entity(
        type=type_,
        client_id=client_id,
        canonical_key=canonical_key,
        display_name=display_name,
        parent_id=parent_id,
        attributes={k: v for k, v in (attributes or {}).items() if v},
        provisional=provisional,
    )
    session.add(e)
    session.flush()
    return e, True


def add_identifier(
    session: Session, entity_id: uuid.UUID, kind: str, raw: str, normalized: str, source: str
) -> None:
    exists = session.scalar(
        select(EntityIdentifier.id).where(
            EntityIdentifier.entity_id == entity_id,
            EntityIdentifier.kind == kind,
            EntityIdentifier.normalized_value == normalized,
        )
    )
    if exists is None:
        session.add(
            EntityIdentifier(
                entity_id=entity_id,
                kind=kind,
                raw_value=raw,
                normalized_value=normalized,
                source=source,
            )
        )
