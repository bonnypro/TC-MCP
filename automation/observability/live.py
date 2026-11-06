"""Live ("实操") event stream for TC-MCP.

Silent mode is the default: nothing is printed and no event file is written.
Set ``TC_MCP_MODE=live`` (or call :func:`configure`) to emit events; the
optional dashboard at ``scripts/live_dashboard.py`` tails the JSONL file and
shows the current phase plus the exact PLC code being written into TwinCAT.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG = ROOT / "runtime" / "live" / "events.jsonl"

_lock = threading.Lock()
_seq = 0
_seq_ready = False
_mode: str | None = None
_log_path: Path | None = None


def _read_mode() -> str:
    value = os.environ.get("TC_MCP_MODE", "silent").strip().lower()
    if value in {"live", "on", "1", "true", "yes"}:
        return "live"
    return "silent"


def configure(mode: str | None = None, log_path: str | Path | None = None) -> None:
    """Set the mode/log path explicitly (mainly for tests)."""
    global _mode, _log_path, _seq, _seq_ready
    _seq = 0
    _seq_ready = False
    if mode is None:
        _mode = _read_mode()
    else:
        _mode = "live" if str(mode).strip().lower() in {"live", "on", "1", "true", "yes"} else "silent"
    if log_path is not None:
        _log_path = Path(log_path)
    else:
        env = os.environ.get("TC_MCP_LIVE_LOG")
        _log_path = Path(env) if env else DEFAULT_LOG


def current_mode() -> str:
    if _mode is None:
        configure()
    return _mode or "silent"


def enabled() -> bool:
    return current_mode() == "live"


def log_path() -> Path:
    if _log_path is None:
        configure()
    return _log_path or DEFAULT_LOG


def _console_code(lines: list[str]) -> None:
    if os.environ.get("TC_MCP_LIVE_CODE", "1") == "0":
        return
    preview = lines[:40]
    print("--- code ---", file=sys.stderr)
    for line in preview:
        print(line, file=sys.stderr)
    if len(lines) > len(preview):
        print(f"... ({len(lines) - len(preview)} more lines)", file=sys.stderr)
    print("--- end code ---", file=sys.stderr)


def _ensure_seq() -> None:
    """Continue the sequence from an existing log (one process at a time)."""
    global _seq, _seq_ready
    if _seq_ready:
        return
    _seq_ready = True
    path = log_path()
    if not path.is_file():
        return
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    value = int(json.loads(line).get("seq", 0))
                except (ValueError, json.JSONDecodeError):
                    continue
                if value > _seq:
                    _seq = value
    except OSError:
        pass


def emit(event: str, message: str = "", **data: object) -> None:
    """Append one event when live mode is active; no-op in silent mode."""
    global _seq
    if not enabled():
        return

    code = data.pop("code", None)
    with _lock:
        _ensure_seq()
        _seq += 1
        payload = {
            "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "seq": _seq,
            "kind": event,
            "message": message,
            "data": data,
        }
        if code is not None:
            payload["data"]["code"] = code

        path = log_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError as exc:
            print(f"[live] cannot write event log: {exc}", file=sys.stderr)

        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[live {stamp}] {event}: {message}".rstrip()
        print(line, file=sys.stderr)
        if code:
            _console_code(str(code).splitlines())
        sys.stderr.flush()
