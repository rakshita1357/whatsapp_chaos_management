"""Version diffs (build brief 8.6). Pure computation first, persistence second."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.workbooks.settings import WorkbookConfig


@dataclass
class RowSnap:
    key: str
    raw: dict[str, Any]  # keyed by canonical field
    norm: dict[str, Any]  # keyed by canonical field (+ "<field>__text" for free text)


@dataclass
class DiffItem:
    row_key: str
    kind: str  # added | removed | changed | format_only | conflict
    column: str | None = None
    old_raw: Any = None
    new_raw: Any = None
    old_norm: Any = None
    new_norm: Any = None
    annotations: list[str] = field(default_factory=list)


@dataclass
class DiffResult:
    items: list[DiffItem]
    summary: dict[str, Any]


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


def _content(row: RowSnap, columns: list[str]) -> tuple[Any, ...]:
    return tuple((row.norm.get(c), row.norm.get(f"{c}__text")) for c in columns)


def group_rows(rows: list[RowSnap], columns: list[str]) -> tuple[dict[str, RowSnap], set[str]]:
    """One snapshot per key; keys whose rows disagree on content are conflicts."""
    by_key: dict[str, list[RowSnap]] = {}
    for r in rows:
        by_key.setdefault(r.key, []).append(r)
    unique: dict[str, RowSnap] = {}
    conflicts: set[str] = set()
    for k, rs in by_key.items():
        if len({_content(r, columns) for r in rs}) > 1:
            conflicts.add(k)
        else:
            unique[k] = rs[0]
    return unique, conflicts


def _is_dm_swap(a: Any, b: Any) -> bool:
    try:
        da, db = date.fromisoformat(str(a)), date.fromisoformat(str(b))
    except ValueError:
        return False
    return da != db and da.day == db.month and da.month == db.day


def compute_diff(
    old_rows: list[RowSnap],
    new_rows: list[RowSnap],
    old_headers: dict[str, str],
    new_headers: dict[str, str],
    cfg: WorkbookConfig,
) -> DiffResult:
    """old_headers/new_headers: canonical -> raw header text, for rename detection."""
    columns = sorted(
        c
        for c in (set(old_headers) | set(new_headers))
        if cfg.field_type(c) != "positional"  # Sr. No. is positional, never diffed (INC-46)
    )
    old, old_conf = group_rows(old_rows, columns)
    new, new_conf = group_rows(new_rows, columns)
    conflicts = old_conf | new_conf
    items: list[DiffItem] = []
    for k in sorted(conflicts):
        items.append(DiffItem(k, "conflict", annotations=["duplicate_key_conflicting_content"]))
    old_keys = {k for k in old if k not in conflicts}
    new_keys = {k for k in new if k not in conflicts}
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    common = sorted(old_keys & new_keys)
    items += [DiffItem(k, "added") for k in added]
    items += [DiffItem(k, "removed") for k in removed]

    group_order = list((cfg.settings.get("diff_column_groups") or {}).keys())
    col_group = {
        c: g for g, cols in (cfg.settings.get("diff_column_groups") or {}).items() for c in cols
    }
    changed_rows: set[str] = set()
    fmt_rows: set[str] = set()
    by_group: dict[str, int] = {}
    by_col: dict[str, int] = {}
    dm_swaps = 0
    for k in common:
        o, n = old[k], new[k]
        row_changed_groups: list[str] = []
        row_has_change = False
        row_has_fmt = False
        for c in columns:
            on, nn = (
                (o.norm.get(c), o.norm.get(f"{c}__text")),
                (n.norm.get(c), n.norm.get(f"{c}__text")),
            )
            orw, nrw = o.raw.get(c), n.raw.get(c)
            if on != nn:
                ann: list[str] = []
                if cfg.field_type(c) == "date" and _is_dm_swap(on[0], nn[0]):
                    ann.append("possible_dm_swap_correction")  # INC-45
                    dm_swaps += 1
                items.append(
                    DiffItem(k, "changed", c, orw, nrw, on[0] or on[1], nn[0] or nn[1], ann)
                )
                row_has_change = True
                by_col[c] = by_col.get(c, 0) + 1
                row_changed_groups.append(col_group.get(c, "other"))
            elif orw != nrw and not (orw in (None, "") and nrw in (None, "")):
                items.append(
                    DiffItem(k, "format_only", c, orw, nrw, on[0] or on[1], nn[0] or nn[1])
                )
                row_has_fmt = True
        if row_has_change:
            changed_rows.add(k)
            ordered = [g for g in [*group_order, "other"] if g in row_changed_groups]
            g0 = ordered[0] if ordered else "other"
            by_group[g0] = by_group.get(g0, 0) + 1
        elif row_has_fmt:
            fmt_rows.add(k)
    renames = [
        {"field": c, "old_header": old_headers[c], "new_header": new_headers[c]}
        for c in sorted(set(old_headers) & set(new_headers))
        if re.sub(r"\s+", " ", old_headers[c]).strip().casefold()
        != re.sub(r"\s+", " ", new_headers[c]).strip().casefold()
    ]
    summary = {
        "added": len(added),
        "removed": len(removed),
        "common": len(common),
        "changed_rows": len(changed_rows),
        "changed_by_group": by_group,
        "changed_by_column": by_col,
        "format_only": len(fmt_rows),
        "conflict": len(conflicts),
        "header_renames": renames,
        "headers_added": sorted(set(new_headers) - set(old_headers)),
        "headers_removed": sorted(set(old_headers) - set(new_headers)),
        "possible_dm_swap_corrections": dm_swaps,
        "removed_keys": removed,
        "added_keys": added,
    }
    return DiffResult(items, summary)
