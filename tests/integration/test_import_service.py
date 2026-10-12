from pathlib import Path

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.core.blobstore import BlobStore
from app.db import models as m
from app.ingest.service import import_path


def test_folder_import_links_media(engine: Engine, tmp_path: Path) -> None:
    d = tmp_path / "export"
    d.mkdir()
    (d / "WhatsApp Chat with Folder Group.txt").write_text(
        "09/10/2026, 1:28 pm - Ravi: P1.jpg (file attached)\nhello\n", encoding="utf-8"
    )
    (d / "P1.jpg").write_bytes(b"img")
    with Session(engine) as s:
        res = import_path(s, d, store=BlobStore(tmp_path / "blobs"))
        s.commit()
        assert res[0][0].status == "PROCESSED"
        a = s.scalar(select(m.Attachment).where(m.Attachment.export_filename == "P1.jpg"))
        assert a is not None and a.availability == "present" and a.sha256
        g = s.scalar(select(m.Group).where(m.Group.name == "Folder Group"))
        assert g is not None
        assert s.scalar(select(func.count()).select_from(m.SourceMessage)) >= 1
