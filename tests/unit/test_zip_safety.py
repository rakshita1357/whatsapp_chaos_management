import zipfile
from pathlib import Path

import pytest

from app.ingest.adapters.whatsapp_export.zip import UnsafeArchiveError, safe_extract


def _zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for n, d in entries.items():
            z.writestr(n, d)
    return path


def test_extracts_normal(tmp_path: Path) -> None:
    z = _zip(tmp_path / "a.zip", {"chat.txt": b"hi", "sub/x.jpg": b"1"})
    files = safe_extract(z, tmp_path / "out")
    assert {f.name for f in files} == {"chat.txt", "x.jpg"}


@pytest.mark.parametrize("name", ["../evil.txt", "/abs.txt", "a/../../b.txt", "C:/x.txt"])
def test_rejects_unsafe_paths(tmp_path: Path, name: str) -> None:
    z = _zip(tmp_path / "a.zip", {name: b"x"})
    with pytest.raises(UnsafeArchiveError):
        safe_extract(z, tmp_path / "out")


def test_caps(tmp_path: Path) -> None:
    z = _zip(tmp_path / "a.zip", {"a": b"x" * 100, "b": b"x"})
    with pytest.raises(UnsafeArchiveError):
        safe_extract(z, tmp_path / "o1", max_bytes=50)
    with pytest.raises(UnsafeArchiveError):
        safe_extract(z, tmp_path / "o2", max_files=1)
