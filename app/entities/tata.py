"""Tata identity model: a dealer code owns many divisions; the division is the site.
Loads MASTER rows into entities (build brief 8.5)."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import load_yaml
from app.db.models import Entity, EntityIdentifier, ReviewItem, WorkbookRow
from app.entities.registry import add_identifier, upsert_entity
from app.workbooks.normalize import division_key
from app.workbooks.settings import WorkbookConfig


@dataclass
class MasterLoadStats:
    dealers: int = 0
    divisions: int = 0
    provisional: int = 0
    duplicate_conflicts: int = 0
    possible_duplicates: int = 0
    invalid_dealer_codes: int = 0
    entity_by_row: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)

    def as_json(self) -> dict[str, int]:
        return {k: v for k, v in self.__dict__.items() if isinstance(v, int)}


def dealer_code_regex(cfg: WorkbookConfig) -> re.Pattern[str]:
    spec = (cfg.groups.get("clients") or {}).get("tata") or (
        (load_yaml("groups") or {}).get("clients", {}).get("tata", {})
    )
    return re.compile(spec.get("dealer_code_regex", r"^300[0-9A-Z]\d{3}$"))


def row_fingerprint(norm: dict[str, Any]) -> str:
    payload = "|".join(
        f"{k}={norm[k]}" for k in sorted(norm) if k != "sr_no" and norm[k] is not None
    )
    return hashlib.sha1(payload.encode()).hexdigest()[:8]  # noqa: S324 - identity, not security


def load_master_rows(
    session: Session,
    rows: list[WorkbookRow],
    client_id: uuid.UUID | None,
    cfg: WorkbookConfig,
    headers: dict[str, str],
    group_id: uuid.UUID | None = None,
) -> MasterLoadStats:
    stats = MasterLoadStats()
    marker = str(cfg.settings.get("new_setup_marker", "new setup")).casefold()
    dealer_re = dealer_code_regex(cfg)
    dealers: dict[str, Entity] = {}
    first_row_for_key: dict[str, WorkbookRow] = {}
    divisions_by_dealer: dict[str, list[tuple[str, str, Entity]]] = {}
    conflicted: set[str] = set()

    for row in rows:
        n = row.normalized
        dealer = n.get("dealer_code")
        division_raw = (row.raw or {}).get(headers.get("division_code", ""))
        raw_div = str(division_raw) if division_raw not in (None, "") else None
        city = n.get("city")
        attrs = {
            k: n.get(k)
            for k in ("city", "hub_city", "state", "pin", "region", "phase", "dealer_name")
        }
        if not dealer:
            continue
        if not dealer_re.match(str(dealer)):
            stats.invalid_dealer_codes += 1
            session.add(
                ReviewItem(
                    kind="invalid_value",
                    group_id=group_id,
                    subject_refs={"workbook_row_id": str(row.id), "field": "dealer_code"},
                    proposed_value={"raw": dealer},
                    note=f"Dealer code {dealer!r} does not match the expected pattern",
                )
            )
        d_ent = dealers.get(str(dealer))
        if d_ent is None:
            d_ent, created = upsert_entity(
                session,
                "dealer_code",
                client_id,
                str(dealer),
                display_name=n.get("dealer_name") or str(dealer),
                attributes={"state": n.get("state"), "region": n.get("region")},
                provisional=not dealer_re.match(str(dealer)),
            )
            stats.dealers += int(created)
            add_identifier(session, d_ent.id, "dealer_code", str(dealer), str(dealer), "master")
            dealers[str(dealer)] = d_ent
        if raw_div is None:
            stats.entity_by_row[row.id] = d_ent.id
            continue
        placeholder = marker in raw_div.casefold()
        if placeholder:
            key = f"newsetup|{dealer}|{str(city or '').casefold()}|{row_fingerprint(n)}"
        else:
            key = division_key(raw_div)
        existing_row = first_row_for_key.get(key)
        if existing_row is not None:
            if _content(existing_row) != _content(row) and key not in conflicted:
                conflicted.add(key)
                stats.duplicate_conflicts += 1
                session.add(
                    ReviewItem(
                        kind="duplicate_division_conflict",
                        group_id=group_id,
                        subject_refs={"division_key": key},
                        candidates=[
                            {
                                "workbook_row_id": str(existing_row.id),
                                "row": existing_row.row_number,
                            },
                            {"workbook_row_id": str(row.id), "row": row.row_number},
                        ],
                        note="Same division key appears twice with different content (INC-24)",
                    )
                )
            existing = session.scalar(
                select(Entity).where(Entity.id == stats.entity_by_row[existing_row.id])
            )
            if existing is not None:
                stats.entity_by_row[row.id] = existing.id
            continue
        first_row_for_key[key] = row
        div_ent, created = upsert_entity(
            session,
            "division",
            client_id,
            key,
            display_name=raw_div,
            parent_id=d_ent.id,
            attributes=attrs,
            provisional=placeholder,
        )
        if created:
            stats.divisions += 1
            if placeholder:
                stats.provisional += 1
                session.add(
                    ReviewItem(
                        kind="entity_match_ambiguous",
                        group_id=group_id,
                        subject_refs={"entity_id": str(div_ent.id), "workbook_row_id": str(row.id)},
                        note="'New Setup' placeholder: needs a real division code",
                    )
                )
        if not placeholder:
            add_identifier(session, div_ent.id, "division_code", raw_div, key, "master")
        stats.entity_by_row[row.id] = div_ent.id
        divisions_by_dealer.setdefault(str(dealer), []).append((key, str(city or ""), div_ent))

    threshold = float(cfg.settings.get("possible_duplicate_city_score", 88))
    for dealer, items in divisions_by_dealer.items():
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                (ka, ca, ea), (kb, cb, eb) = items[i], items[j]
                if (
                    ca
                    and cb
                    and ca.casefold() != cb.casefold()
                    and fuzz.ratio(ca.casefold(), cb.casefold()) >= threshold
                ):
                    stats.possible_duplicates += 1
                    session.add(
                        ReviewItem(
                            kind="possible_duplicate_entity",
                            group_id=group_id,
                            subject_refs={"dealer_code": dealer},
                            candidates=[
                                {"entity_id": str(ea.id), "division_key": ka, "city": ca},
                                {"entity_id": str(eb.id), "division_key": kb, "city": cb},
                            ],
                            note="Similar city spellings under one dealer (INC-23)",
                        )
                    )
    session.flush()
    return stats


def _content(row: WorkbookRow) -> tuple[Any, ...]:
    return tuple(sorted((k, str(v)) for k, v in row.normalized.items() if k != "sr_no"))


def find_division_entities(
    session: Session, client_id: uuid.UUID | None, keys: list[str]
) -> dict[str, uuid.UUID]:
    if not keys:
        return {}
    q = select(Entity.canonical_key, Entity.id).where(
        Entity.type == "division", Entity.canonical_key.in_(keys)
    )
    q = q.where(Entity.client_id == client_id) if client_id else q.where(Entity.client_id.is_(None))
    return {k: i for k, i in session.execute(q).all()}


__all__ = ["load_master_rows", "MasterLoadStats", "find_division_entities", "EntityIdentifier"]
