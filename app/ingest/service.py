"""Import a chat from a .txt/.md file, a folder, or a .zip (CLI and /imports share this)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.blobstore import BlobStore, sha256_file
from app.core.config import get_settings
from app.db.models import Import
from app.ingest.adapters.whatsapp_export.detect import (
    PARSERS,
    detect_format,
    group_name_from_filename,
)
from app.ingest.adapters.whatsapp_export.zip import safe_extract
from app.ingest.merge import ImportStats, persist
from app.workbooks.attachments import ingest_workbook_attachments

_TEXT_EXT = {".txt", ".md"}


def import_path(
    session: Session, path: Path, group_name: str | None = None, store: BlobStore | None = None
) -> list[tuple[Import, ImportStats]]:
    s = get_settings()
    store = store or BlobStore(s.blob_dir)
    if path.suffix.lower() == ".zip":
        with tempfile.TemporaryDirectory(prefix="wcm-zip-") as tmp:
            safe_extract(path, Path(tmp) / "x", s.zip_max_uncompressed_bytes, s.zip_max_files)
            return import_path(session, Path(tmp) / "x", group_name, store)
    if path.is_dir():
        files = {p.name: p for p in path.rglob("*") if p.is_file()}
        texts = [p for p in files.values() if p.suffix.lower() in _TEXT_EXT]
        media = {n: p for n, p in files.items() if p not in texts}
        out = []
        for t in sorted(texts):
            out += _import_text(session, t, group_name, media, store)
        return out
    return _import_text(session, path, group_name, {}, store)


def _import_text(
    session: Session, t: Path, group_name: str | None, media: dict[str, Path], store: BlobStore
) -> list[tuple[Import, ImportStats]]:
    text = t.read_text(encoding="utf-8-sig")
    fmt = detect_format(text)
    if fmt is None:
        return []
    name = group_name or group_name_from_filename(t)
    result = PARSERS[fmt](text, name)
    name = name or result.group_name
    if not name:
        raise ValueError(f"cannot determine group name for {t.name}; pass --group")
    imp, stats = persist(
        session, result, name, adapter=fmt, file_sha256=sha256_file(t), files=media, store=store
    )
    if imp.group_id is not None and imp.status == "PROCESSED":
        session.flush()
        wb = ingest_workbook_attachments(session, store, imp.group_id, imp)
        if wb:
            imp.stats = {**imp.stats, "workbooks": wb}
    return [(imp, stats)]
