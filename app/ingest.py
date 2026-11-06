from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logging.getLogger("pypdf").setLevel(logging.ERROR)

from . import db

TEXT_SUFFIXES = {".txt"}
MAX_FILE_BYTES = 80 * 1024 * 1024


def _pdf_to_text(path: Path) -> tuple[str, str]:
    try:
        import pymupdf  # type: ignore

        document = pymupdf.open(str(path))
        title = ""
        if document.metadata and document.metadata.get("title"):
            title = str(document.metadata["title"])
        pages = [page.get_text() or "" for page in document]
        document.close()
        return title, "\n\n".join(pages)
    except Exception:
        pass
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("neither pymupdf nor pypdf is installed") from exc
    reader = PdfReader(str(path))
    title = ""
    if reader.metadata and reader.metadata.title:
        title = str(reader.metadata.title)
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return title, "\n\n".join(pages)


def ingest_file(
    conn: sqlite3.Connection,
    path: Path,
    *,
    source_id: str = "local",
    version: str = "",
    url: str = "",
) -> tuple[bool, str]:
    if not path.is_file():
        return False, "not a file"
    if path.stat().st_size > MAX_FILE_BYTES:
        return False, "too large"
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        title, text = _pdf_to_text(path)
    elif suffix in TEXT_SUFFIXES or (suffix == "" and path.name.upper() in {"LICENSE", "README", "NOTICE"}):
        text = path.read_text(encoding="utf-8", errors="ignore")
        title = ""
    else:
        return False, "unsupported"

    title = title.strip() or path.stem
    if not text.strip():
        return False, "empty text"
    _, created = db.add_document(
        conn,
        source_id=source_id,
        url=url or str(path.resolve()),
        title=title,
        section=path.parent.name,
        version=version,
        text=text,
    )
    return created, "ingested" if created else "duplicate"


def ingest_directory(
    conn: sqlite3.Connection,
    directory: Path,
    *,
    source_id: str = "local",
    skip_names: tuple[str, ...] = (),
) -> dict[str, int]:
    result = {"seen": 0, "ingested": 0, "skipped": 0, "duplicate": 0, "errors": 0}
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if any(name in str(path) for name in skip_names):
            continue
        result["seen"] += 1
        try:
            ok, reason = ingest_file(conn, path, source_id=source_id)
            if ok:
                result["ingested"] += 1
            elif reason == "duplicate":
                result["duplicate"] += 1
            else:
                result["skipped"] += 1
        except Exception:
            result["errors"] += 1
    return result
