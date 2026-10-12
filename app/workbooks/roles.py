"""Role inference and family keys (build brief 8.4)."""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePath

from app.workbooks.settings import WorkbookConfig

_DATE_PATTERNS = [
    r"\d{1,2}[-_.]\d{1,2}[-_.]\d{2,4}",
    r"\d{4}[-_.]\d{1,2}[-_.]\d{1,2}",
    r"\d{8}",
]


def infer_role(
    sheet_names: list[str],
    primary_rows: int,
    readiness_values: list[str | None],
    only_pivots: bool,
    cfg: WorkbookConfig,
) -> str:
    """readiness_values: canonical readiness per data row (None when missing)."""
    if any(re.search(r"implementation\s*plan", n, re.IGNORECASE) for n in sheet_names):
        return "IMPLEMENTATION_PLAN"
    if only_pivots:
        return "SUMMARY"
    ready_vals = set(cfg.settings.get("ready_values", ["ready"]))
    filled = [v for v in readiness_values if v]
    if filled and all(v in ready_vals for v in filled) and len(filled) == primary_rows:
        return "READY_LIST"
    if primary_rows >= int(cfg.settings.get("master_min_rows", 1000)) and len(set(filled)) > 1:
        return "MASTER"
    return "UNKNOWN"


def family_stem(filename: str) -> str:
    stem = PurePath(filename).stem.casefold()
    for pat in _DATE_PATTERNS:  # dates first, so "-2026" is never mistaken for a copy suffix
        stem = re.sub(pat, "", stem)
    stem = re.sub(r"\s*(\(\d+\)|-\d+)\s*$", "", stem.strip(" _-."))  # "-1" / "(1)" copies
    return re.sub(r"[\s_\-.]+", " ", stem).strip()


def header_signature(canonicals: list[str | None]) -> str:
    names = sorted({c for c in canonicals if c})
    return hashlib.sha1("|".join(names).encode()).hexdigest()[:8]  # noqa: S324


def family_key(filename: str, role: str, canonicals: list[str | None]) -> str:
    return f"{family_stem(filename)}|{role}|{header_signature(canonicals)}"
