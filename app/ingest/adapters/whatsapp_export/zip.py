"""Safe zip extraction (build brief 6.6): no absolute paths, no `..`, size and count caps."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path, PurePosixPath


class UnsafeArchiveError(ValueError):
    pass


def safe_extract(
    zip_path: Path, dest: Path, max_bytes: int = 2 * 1024**3, max_files: int = 20000
) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    if any(dest.iterdir()):
        raise UnsafeArchiveError("destination must be a fresh empty directory")
    root = dest.resolve()
    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if len(infos) > max_files:
            raise UnsafeArchiveError(f"too many entries ({len(infos)} > {max_files})")
        if sum(i.file_size for i in infos) > max_bytes:
            raise UnsafeArchiveError("uncompressed size exceeds cap")
        for info in infos:
            name = info.filename.replace("\\", "/")
            p = PurePosixPath(name)
            if p.is_absolute() or ".." in p.parts or (len(name) > 1 and name[1] == ":"):
                raise UnsafeArchiveError(f"unsafe path: {info.filename!r}")
            if (info.external_attr >> 28) == 0xA:  # symlink
                raise UnsafeArchiveError(f"symlink entry: {info.filename!r}")
            target = (root / name).resolve()
            if not target.is_relative_to(root):
                raise UnsafeArchiveError(f"path escapes destination: {info.filename!r}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)  # never executed, only written
            extracted.append(target)
    return extracted
