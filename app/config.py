from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB_PATH = DATA / "knowledge.sqlite"
SOURCES_PATH = ROOT / "config" / "sources.json"
STATE_PATH = DATA / "update_state.json"
AUDIT_PATH = DATA / "audit.jsonl"
BACKUPS = DATA / "backups"
OFFICIAL_TEXT = DATA / "official_text"
PDF_STORE = DATA / "pdfs"
UPDATES = ROOT / "updates"
VENDOR = ROOT / "vendor"
TMP = ROOT / "tmp"


def ensure_dirs() -> None:
    for path in (DATA, BACKUPS, OFFICIAL_TEXT, PDF_STORE, UPDATES, VENDOR, TMP, ROOT / "runtime"):
        path.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
