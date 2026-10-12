"""Content-addressed, immutable blob store: data/blobs/sha256/ab/cd/<hash>."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO

_CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def key_for(digest: str) -> str:
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("not a sha256 hex digest")
        return f"sha256/{digest[:2]}/{digest[2:4]}/{digest}"

    def path_for_key(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not p.is_relative_to(self.root.resolve()):
            raise ValueError("storage key escapes the blob root")
        return p

    def exists(self, digest: str) -> bool:
        return self.path_for_key(self.key_for(digest)).exists()

    def put_file(self, src: Path) -> tuple[str, str, bool]:
        """Store a file; returns (sha256, storage_key, newly_stored). Duplicates reuse the blob."""
        digest = sha256_file(src)
        with src.open("rb") as fh:
            return self._put(digest, fh)

    def put_bytes(self, data: bytes) -> tuple[str, str, bool]:
        digest = hashlib.sha256(data).hexdigest()
        tmp = Path(tempfile.mkstemp(dir=self._tmpdir())[1])
        tmp.write_bytes(data)
        try:
            with tmp.open("rb") as fh:
                return self._put(digest, fh)
        finally:
            tmp.unlink(missing_ok=True)

    def open(self, key: str) -> BinaryIO:
        return self.path_for_key(key).open("rb")

    def delete(self, key: str) -> None:
        """Only for D2 hard-deletes, after confirming no attachment row shares the hash."""
        self.path_for_key(key).unlink(missing_ok=True)

    def _tmpdir(self) -> Path:
        t = self.root / "_tmp"
        t.mkdir(parents=True, exist_ok=True)
        return t

    def _put(self, digest: str, src: BinaryIO) -> tuple[str, str, bool]:
        key = self.key_for(digest)
        dest = self.path_for_key(key)
        if dest.exists():
            return digest, key, False
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self._tmpdir())
        try:
            with os.fdopen(fd, "wb") as out:
                shutil.copyfileobj(src, out, _CHUNK)
            os.chmod(tmp_name, 0o444)
            os.replace(tmp_name, dest)  # atomic; blobs are immutable once placed
        finally:
            Path(tmp_name).unlink(missing_ok=True)
        return digest, key, True
