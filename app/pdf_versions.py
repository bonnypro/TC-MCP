from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from .config import PDF_STORE, ROOT, STATE_PATH, load_json, save_json
from .sources import flatten_documents

VERSIONS_PATH = ROOT / "data" / "pdf_versions.json"
VERSION_RE = re.compile(r"(?:Version|version|VERSION)\s*[:=]?\s*([0-9]+(?:\.[0-9]+){1,3})")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_version(path: Path) -> str:
    try:
        import pymupdf  # type: ignore

        document = pymupdf.open(str(path))
        sample = "\n".join((document[i].get_text() or "") for i in range(min(3, document.page_count)))
        document.close()
    except Exception:
        sample = ""
    match = VERSION_RE.search(sample)
    if match:
        return match.group(1)
    text_path = ROOT / "data" / "official_text" / f"{path.stem}.txt"
    if text_path.exists():
        match = VERSION_RE.search(text_path.read_text(encoding="utf-8", errors="ignore")[:20000])
        if match:
            return match.group(1)
    return "unknown"


def record() -> Path:
    state = load_json(STATE_PATH, {})
    documents = flatten_documents()
    records = []
    for doc in documents:
        if doc.get("kind") != "pdf":
            continue
        doc_id = str(doc.get("id", ""))
        path = PDF_STORE / f"{doc_id}.pdf"
        if not path.exists():
            continue
        old = state.get(doc_id, {})
        version = _read_version(path)
        records.append(
            {
                "id": doc_id,
                "title": path.stem,
                "url": doc.get("url", ""),
                "version": version,
                "size": path.stat().st_size,
                "sha256": _sha256(path),
                "etag": old.get("etag", ""),
                "last_modified": old.get("last_modified", ""),
                "signature": old.get("signature", ""),
                "downloaded_at": datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc
                ).isoformat(timespec="seconds"),
            }
        )
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(records),
        "pdfs": sorted(records, key=lambda item: item["id"]),
    }
    save_json(VERSIONS_PATH, payload)
    return VERSIONS_PATH


if __name__ == "__main__":
    print(record())
