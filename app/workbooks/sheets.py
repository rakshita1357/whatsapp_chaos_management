"""Sheet kind and header-row detection (build brief 8.2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.workbooks.open import RawSheet
from app.workbooks.settings import WorkbookConfig, norm_header

PIVOT_MARKERS = ("row labels", "grand total")
PIVOT_PREFIXES = ("count of ", "sum of ", "average of ")


@dataclass
class SheetInfo:
    kind: str  # data_table | pivot_summary | helper_or_empty
    header_row: int | None  # 1-based row number in the sheet
    alias_hits: int = 0


def _hits(row: list[Any], cfg: WorkbookConfig) -> int:
    seen = set()
    for c in row:
        if c is None:
            continue
        canonical = cfg.alias_index.get(norm_header(c))
        if canonical:
            seen.add(canonical)
    return len(seen)


def classify_sheet(sheet: RawSheet, cfg: WorkbookConfig) -> SheetInfo:
    scan = int(cfg.settings.get("header_scan_rows", 15))
    need = int(cfg.settings.get("min_header_alias_hits", 5))
    head = sheet.rows[:scan]
    for r in head:
        for c in r:
            t = norm_header(c)
            if t in PIVOT_MARKERS or t.startswith(PIVOT_PREFIXES):
                return SheetInfo("pivot_summary", None)
    best: tuple[int, int] = (0, -1)  # (hits, index)
    for i, r in enumerate(head):
        h = _hits(r, cfg)
        if h > best[0]:
            best = (h, i)
    if best[0] >= need:
        return SheetInfo("data_table", best[1] + 1, best[0])
    return SheetInfo("helper_or_empty", None, best[0])
