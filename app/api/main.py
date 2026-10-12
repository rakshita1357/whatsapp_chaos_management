from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Import, ReviewItem
from app.db.session import get_engine
from app.ingest.service import import_path

app = FastAPI(title="WhatsApp Chaos Management")


def db() -> Any:
    with Session(get_engine()) as s:
        yield s
        s.commit()


def current_user(x_wcm_user: Annotated[str | None, Header()] = None) -> str:
    """Dev auth only (WCM_DEV_AUTH=1): X-WCM-User selects admin/tata_viewer/maruti_viewer."""
    if not get_settings().dev_auth:
        raise HTTPException(401, "authentication not configured")
    if x_wcm_user not in {"admin", "tata_viewer", "maruti_viewer"}:
        raise HTTPException(401, "unknown user")
    return x_wcm_user


@app.get("/health")
def health(s: Annotated[Session, Depends(db)]) -> dict[str, Any]:
    open_reviews = s.scalar(
        select(func.count()).select_from(ReviewItem).where(ReviewItem.status == "open")
    )
    return {"status": "ok", "open_review_items": open_reviews}


@app.post("/imports")
async def create_import(
    s: Annotated[Session, Depends(db)],
    user: Annotated[str, Depends(current_user)],
    file: Annotated[UploadFile, File()],
    group: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    if user != "admin":
        raise HTTPException(403, "only admin may import")
    suffix = Path(file.filename or "upload").suffix.lower()
    if suffix not in {".txt", ".md", ".zip"}:
        raise HTTPException(415, "upload a .txt, .md or .zip chat export")
    with tempfile.TemporaryDirectory(prefix="wcm-up-") as tmp:
        dest = Path(tmp) / f"upload{suffix}"
        dest.write_bytes(await file.read())
        if suffix != ".zip" and group is None and file.filename:
            # keep the original name so "WhatsApp Chat with <group>.txt" can name the group
            named = Path(tmp) / Path(file.filename).name
            dest.rename(named)
            dest = named
        try:
            results = import_path(s, dest, group)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    if not results:
        raise HTTPException(422, "no recognizable WhatsApp export found")
    imp: Import = results[0][0]
    return {
        "import_id": str(imp.id),
        "status": imp.status,
        "stats": results[0][1].as_json(),
        "warnings": imp.warnings,
        "date_order": imp.date_order,
    }
