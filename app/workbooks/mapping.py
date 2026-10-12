"""Header -> canonical field mapping (build brief 8.3). Fuzzy matches are proposals only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz, process

from app.workbooks.settings import WorkbookConfig, norm_header


@dataclass
class ColumnMap:
    index: int  # 0-based column
    header_raw: str  # unique key used in `raw` (suffixed #2.. when a header repeats)
    canonical: str | None
    method: str  # exact | fuzzy | unknown
    score: float = 100.0

    def as_json(self) -> dict[str, Any]:
        return self.__dict__.copy()


def map_headers(header_cells: list[Any], cfg: WorkbookConfig) -> list[ColumnMap]:
    out: list[ColumnMap] = []
    seen_raw: dict[str, int] = {}
    used: set[str] = set()
    threshold = float(cfg.settings.get("fuzzy_header_min_score", 90))
    keys = list(cfg.alias_index)
    for i, cell in enumerate(header_cells):
        if cell is None or str(cell).strip() == "":
            continue
        raw = str(cell).replace(" ", " ").strip()
        seen_raw[raw] = seen_raw.get(raw, 0) + 1
        uniq = raw if seen_raw[raw] == 1 else f"{raw}#{seen_raw[raw]}"
        n = norm_header(raw)
        canonical = cfg.alias_index.get(n)
        if canonical and canonical not in used:
            out.append(ColumnMap(i, uniq, canonical, "exact"))
            used.add(canonical)
            continue
        match = process.extractOne(n, keys, scorer=fuzz.ratio) if keys and n else None
        if match and match[1] >= threshold and cfg.alias_index[match[0]] not in used:
            canonical = cfg.alias_index[match[0]]
            out.append(ColumnMap(i, uniq, canonical, "fuzzy", float(match[1])))
            used.add(canonical)
        else:
            out.append(ColumnMap(i, uniq, None, "unknown", float(match[1]) if match else 0.0))
    return out
