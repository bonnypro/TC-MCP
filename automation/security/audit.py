from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from automation.security.policy import WRITE_ROOT, ensure_within_workspace


class AuditLog:
    """Append-only JSONL audit trail for automation runs."""

    def __init__(self, path: Path | None = None, workspace: Path = WRITE_ROOT) -> None:
        self.workspace = workspace
        self.path = ensure_within_workspace(path or workspace / "audit.jsonl", workspace)

    def record(self, event: str, **fields) -> None:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pid": os.getpid(),
            "event": event,
            **fields,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
