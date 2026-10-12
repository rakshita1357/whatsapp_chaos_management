"""Usage: uv run python scripts/import_chat.py <file|folder|zip> [--group NAME]"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.core.logging import setup_logging
from app.db.session import session_scope
from app.ingest.service import import_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path)
    ap.add_argument("--group")
    a = ap.parse_args()
    setup_logging()
    with session_scope() as s:
        for imp, stats in import_path(s, a.path, a.group):
            print(imp.id, imp.status, json.dumps(stats.as_json()))


if __name__ == "__main__":
    main()
