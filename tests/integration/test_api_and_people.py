import io
import zipfile

from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models as m
from app.entities.people import seed_people
from app.ingest.adapters.whatsapp_export import alt_txt, android_native
from app.ingest.merge import persist


def _wipe(engine: Engine) -> None:
    with Session(engine) as s:
        for t in (
            m.Attachment,
            m.SourceMessage,
            m.ReviewItem,
            m.Import,
            m.PersonAlias,
            m.Person,
            m.Group,
        ):
            s.execute(delete(t))
        s.commit()


def test_name_label_resolves_to_phone_key_so_formats_merge(engine: Engine) -> None:
    _wipe(engine)
    G = "People Group"
    with Session(engine) as s:
        seed_people(
            s, {"people": [{"sender_key": "918848991765", "name": "Ravi Kumar", "team": "Ops"}]}
        )
        persist(
            s,
            android_native.parse("09/10/2026, 1:28 pm - +91 88489 91765: ok\n", G),
            G,
            adapter="t",
        )
        persist(s, alt_txt.parse("[10/9/26, 1:28:10 PM] Ravi Kumar: ok\n", G), G, adapter="t")
        s.commit()
        assert len(s.scalars(select(m.SourceMessage)).all()) == 1


def test_imports_endpoint(engine: Engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _wipe(engine)
    from app.api import main

    monkeypatch.setattr(main, "get_engine", lambda: engine)
    monkeypatch.setattr(get_settings(), "dev_auth", True)
    c = TestClient(main.app)
    body = b"09/10/2026, 1:28 pm - Ravi: hello\n"
    assert c.post("/imports", files={"file": ("x.txt", body)}).status_code == 401
    h = {"X-WCM-User": "tata_viewer"}
    assert c.post("/imports", headers=h, files={"file": ("x.txt", body)}).status_code == 403
    h = {"X-WCM-User": "admin"}
    r = c.post("/imports", headers=h, files={"file": ("WhatsApp Chat with Api Group.txt", body)})
    assert r.status_code == 200, r.text
    assert r.json()["stats"]["created"] == 1
    assert c.post("/imports", headers=h, files={"file": ("a.exe", b"x")}).status_code == 415
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("WhatsApp Chat with Zip Group.txt", body.decode())
    r = c.post("/imports", headers=h, files={"file": ("e.zip", buf.getvalue())})
    assert r.status_code == 200 and r.json()["stats"]["created"] == 1
    assert c.get("/health").json()["status"] == "ok"
