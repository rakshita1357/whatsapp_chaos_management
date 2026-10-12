"""Workbook config bundle, loaded from config/*.yaml (nothing hard-coded here)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import load_yaml


def norm_header(s: Any) -> str:
    """Header normalization: casefold, NBSP to space, drop punctuation, collapse whitespace."""
    t = str(s if s is not None else "").replace(" ", " ").casefold()
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


@dataclass
class WorkbookConfig:
    fields: dict[str, dict[str, Any]]
    alias_index: dict[str, str]  # normalized alias -> canonical
    status_map: dict[str, dict[str, dict[str, str]]]
    settings: dict[str, Any]
    groups: dict[str, Any] = field(default_factory=dict)

    def field_type(self, canonical: str) -> str:
        return str(self.fields.get(canonical, {}).get("type", "text"))

    def enum_lookup(self, role: str, canonical: str, value: str) -> str | None:
        key = value.strip().casefold()
        for r in (role, "ANY"):
            hit = self.status_map.get(r, {}).get(canonical, {}).get(key)
            if hit is not None:
                return hit
        return None


def load_workbook_config(config_dir: Path | None = None) -> WorkbookConfig:
    fields = (load_yaml("header_aliases", config_dir) or {}).get("fields", {})
    index: dict[str, str] = {}
    for canonical, spec in fields.items():
        index[norm_header(canonical)] = canonical
        for alias in spec.get("aliases", []):
            index[norm_header(alias)] = canonical
    raw_map = load_yaml("status_map", config_dir) or {}
    smap: dict[str, dict[str, dict[str, str]]] = {}
    for role, by_field in raw_map.items():
        smap[role] = {
            f: {str(k).strip().casefold(): str(v) for k, v in vals.items()}
            for f, vals in (by_field or {}).items()
        }
    return WorkbookConfig(
        fields=fields,
        alias_index=index,
        status_map=smap,
        settings=load_yaml("workbooks", config_dir) or {},
        groups=load_yaml("groups", config_dir) or {},
    )
