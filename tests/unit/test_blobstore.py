import hashlib
from pathlib import Path

import pytest

from app.core.blobstore import BlobStore


def test_put_is_content_addressed_and_deduplicated(tmp_path: Path) -> None:
    store = BlobStore(tmp_path / "blobs")
    d, key, new = store.put_bytes(b"hello")
    assert d == hashlib.sha256(b"hello").hexdigest()
    assert key == f"sha256/{d[:2]}/{d[2:4]}/{d}"
    assert new is True
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello")
    d2, key2, new2 = store.put_file(f)
    assert (d2, key2, new2) == (d, key, False)
    with store.open(key) as fh:
        assert fh.read() == b"hello"


def test_blob_is_read_only(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    _, key, _ = store.put_bytes(b"abc")
    assert not (store.path_for_key(key).stat().st_mode & 0o222)


def test_key_traversal_rejected(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    with pytest.raises(ValueError):
        store.path_for_key("../../etc/passwd")
    with pytest.raises(ValueError):
        BlobStore.key_for("nothex")
