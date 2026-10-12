"""Import one workbook directly (outside a chat).

Usage:
    uv run python scripts/import_workbook.py <file.xlsx> --posted-at 2026-10-09T10:00:00+05:30
    (optional: --posted-by NAME, --client tata)

--posted-at is required: versions are ordered by when the file was posted,
never by filename or mtime.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from app.core.logging import setup_logging
from app.db.session import session_scope
from app.workbooks.ingest import import_workbook


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path)
    ap.add_argument(
        "--posted-at", required=True, help="ISO time with offset, e.g. 2026-10-09T10:00:00+05:30"
    )
    ap.add_argument("--posted-by")
    ap.add_argument(
        "--client", default=None, help="client key (default from config/workbooks.yaml)"
    )
    ap.add_argument(
        "--replace",
        action="store_true",
        help="delete the existing import of this exact file first, then import it again",
    )
    a = ap.parse_args()
    posted = datetime.fromisoformat(a.posted_at)
    if posted.tzinfo is None:
        raise SystemExit("--posted-at needs a timezone offset, e.g. +05:30")
    setup_logging()
    with session_scope() as s:
        if a.replace:
            from sqlalchemy import select

            from app.core.blobstore import sha256_file
            from app.db.models import WorkbookImport
            from app.workbooks.ingest import delete_workbook_import

            old = s.scalar(
                select(WorkbookImport).where(WorkbookImport.sha256 == sha256_file(a.path))
            )
            if old is not None:
                delete_workbook_import(s, old, "re-import with --replace")
                print("deleted previous import", old.id)
        res = import_workbook(
            s,
            a.path,
            filename=a.path.name,
            posted_at=posted,
            posted_by=a.posted_by,
            client_key=a.client,
        )
        print("created" if res.created else "already imported", json.dumps(res.stats, default=str))
        if res.import_ is not None:
            print("workbook_import id:", res.import_.id, "version", res.import_.version_no)


if __name__ == "__main__":
    main()
