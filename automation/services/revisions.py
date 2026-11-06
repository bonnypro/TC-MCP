from __future__ import annotations

import hashlib
from pathlib import Path

SOURCE_SUFFIXES = {
    ".sln",
    ".tsproj",
    ".plcproj",
    ".tcpou",
    ".tcdut",
    ".tcgvl",
    ".tctto",
    ".xti",
    ".tcvis",
}
SKIP_DIRS = {"_Boot", "_CompileInfo", ".vs", "bin", "obj", "__pycache__"}


def project_revision(root: Path) -> dict:
    root = root.resolve()
    entries: list[str] = []
    if root.exists():
        for file in root.rglob("*"):
            if not file.is_file():
                continue
            if any(part in SKIP_DIRS for part in file.parts):
                continue
            if file.suffix.casefold() not in SOURCE_SUFFIXES:
                continue
            digest = hashlib.sha256(file.read_bytes()).hexdigest()
            entries.append(f"{file.relative_to(root).as_posix()}\0{digest}")
    revision = hashlib.sha256("\n".join(sorted(entries)).encode("utf-8")).hexdigest()[:16]
    return {"revision": revision, "files": len(entries)}
