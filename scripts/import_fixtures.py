"""Imports everything under fixtures/whatsapp_exports (native first, then alt formats)."""

from pathlib import Path

from app.db.session import session_scope
from app.ingest.service import import_path

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "whatsapp_exports"

if __name__ == "__main__":
    if not ROOT.exists():
        raise SystemExit(f"{ROOT} not found: fixtures have not been supplied yet")
    with session_scope() as s:
        for sub in ("native_android", "alt_tool"):
            for d in sorted((ROOT / sub).glob("*")):
                for imp, st in import_path(s, d):
                    print(sub, d.name, imp.status, st.as_json())
