from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOCK_PATH = Path(tempfile.gettempdir()) / "tc-mcp-runner.lock"


class RunnerBusy(RuntimeError):
    """Another TC-MCP runner owns the simulation resources."""


class RunLock:
    """Cross-process lock for the single simulation runner.

    The OS releases the lock automatically if the process crashes. The file
    contents are diagnostic only; the byte lock is authoritative.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else Path(
            os.environ.get("TC_MCP_RUN_LOCK", DEFAULT_LOCK_PATH)
        )
        self._fd = None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = open(self.path, "a+b")
        try:
            fd.seek(0)
            fd.write(b" ")
            fd.flush()
            fd.seek(0)
            import msvcrt

            msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            fd.close()
            raise RunnerBusy(f"another simulation runner is active: {self.path}") from exc
        self._fd = fd
        fd.seek(0)
        fd.truncate()
        fd.write(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "path": str(self.path),
                },
                ensure_ascii=False,
            ).encode("utf-8")
        )
        fd.flush()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fd is None:
            return
        try:
            import msvcrt

            self._fd.seek(0)
            msvcrt.locking(self._fd.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self._fd.close()
            self._fd = None
