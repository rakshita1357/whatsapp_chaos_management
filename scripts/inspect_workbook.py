"""Print a privacy-safe summary of a workbook: sheets, headers and the distinct values of
status-like columns (no names, phones or addresses). Safe to paste into a chat.

Usage: uv run python scripts/inspect_workbook.py <file.xlsx>
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from app.workbooks.mapping import map_headers
from app.workbooks.open import open_workbook
from app.workbooks.settings import load_workbook_config
from app.workbooks.sheets import classify_sheet

SAFE_FIELDS = {
    "readiness", "installation_status", "dispatch_status", "contacted", "phase", "state",
    "record_date", "planned_install_date", "expected_timeline", "dispatch_date", "region",
}  # fmt: skip
MAX_VALUES = 25


def main() -> None:
    path = Path(sys.argv[1])
    cfg = load_workbook_config()
    wb = open_workbook(path)
    print(f"opened_via: {wb.opened_via}")
    for w in wb.warnings:
        print("warning:", w[:160])
    for sheet in wb.sheets:
        info = classify_sheet(sheet, cfg)
        print(f"\n=== sheet {sheet.name!r}: kind={info.kind} visible={sheet.visible} "
              f"rows={sheet.max_row} cols={sheet.max_col_effective}")  # fmt: skip
        if info.header_row is None:
            first = [str(c) for c in (sheet.rows[0] if sheet.rows else []) if c is not None]
            print("first row:", first[:25])
            continue
        header = sheet.rows[info.header_row - 1]
        cols = map_headers(header, cfg)
        print("headers:", [c.header_raw for c in cols])
        unmapped = [c.header_raw for c in cols if c.canonical is None]
        print("not mapped to a known field:", unmapped)
        for c in cols:
            if c.canonical not in SAFE_FIELDS:
                continue
            counts = Counter(
                str(r[c.index]).strip()
                if c.index < len(r) and r[c.index] is not None
                else "(blank)"
                for r in sheet.rows[info.header_row :]
            )
            print(f"\n  {c.header_raw}  ->  {c.canonical}   ({len(counts)} distinct)")
            for v, n in counts.most_common(MAX_VALUES):
                print(f"    {n:5d}  {v[:60]}")


if __name__ == "__main__":
    main()
