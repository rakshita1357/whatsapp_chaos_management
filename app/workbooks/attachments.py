"""Feed workbooks posted in chats into the workbook pipeline; the posting message's time
(not the filename or mtime) orders versions."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.blobstore import BlobStore
from app.db.models import Attachment, Group, Import, SourceMessage, WorkbookImport
from app.workbooks.ingest import import_workbook


def ingest_workbook_attachments(
    session: Session, store: BlobStore, group_id: uuid.UUID, imp: Import | None = None
) -> list[dict[str, Any]]:
    group = session.get(Group, group_id)
    client_key: str | None = None
    if group is not None and group.client_id is not None:
        from app.db.models import Client

        c = session.get(Client, group.client_id)
        client_key = c.key if c else None
    rows = session.execute(
        select(Attachment, SourceMessage)
        .join(SourceMessage, SourceMessage.id == Attachment.message_id)
        .where(
            SourceMessage.group_id == group_id,
            Attachment.doc_kind == "workbook",
            Attachment.storage_key.is_not(None),
        )
        .order_by(SourceMessage.sent_at)
    ).all()
    out: list[dict[str, Any]] = []
    for att, msg in rows:
        done = session.scalar(
            select(WorkbookImport.id).where(WorkbookImport.attachment_id == att.id)
        )
        if done is not None or att.storage_key is None:
            continue
        name = att.original_filename or att.export_filename or "workbook.xlsx"
        try:
            res = import_workbook(
                session,
                store.path_for_key(att.storage_key),
                filename=name,
                posted_at=msg.sent_at,
                posted_by=msg.sender_label,
                attachment_id=att.id,
                group_id=group_id,
                client_key=client_key,
            )
            out.append({"attachment_id": str(att.id), "created": res.created, **res.stats})
            att.parse_state = "PROCESSED" if res.import_ is not None else "NEEDS_REVIEW"
        except Exception as exc:  # noqa: BLE001 - one bad workbook must not fail the chat import
            att.parse_state = "FAILED_RETRYABLE"
            out.append(
                {"attachment_id": str(att.id), "error": f"{type(exc).__name__}: {exc}"[:200]}
            )
            if imp is not None:
                imp.warnings = [*imp.warnings, f"workbook {name} failed: {type(exc).__name__}"]
    return out


__all__ = ["ingest_workbook_attachments"]
