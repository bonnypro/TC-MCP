"""Local control server for the DSH TwinCAT side panel.

Accepts a complete ``PROGRAM MAIN`` body from the panel, writes it into a copy
of ``templates/minimal``, builds and activates it.  Run/Stop are asynchronous
so the browser can poll ``/status`` while TwinCAT is restarting.

    python scripts/code_server.py --http-port 8767
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TEMPLATE = ROOT / "templates" / "minimal" / "Minimal.sln"
NETID = "127.0.0.1.1.1"
PLC_PORT = 851
PROJECT_NAME = "TwinCATPanel"

DEFAULT_MAIN = """PROGRAM MAIN
VAR
    rPhase : LREAL;
    rSine : LREAL;
    rCos : LREAL;
END_VAR

rPhase := rPhase + 0.05;
IF rPhase > 6.283185 THEN
    rPhase := 0.0;
END_IF
rSine := SIN(rPhase) * 10.0;
rCos := COS(rPhase) * 10.0;"""

DEFAULT_AUX = """FUNCTION_BLOCK FB_Aux
VAR_INPUT
    rIn : LREAL;
END_VAR
VAR_OUTPUT
    rOut : LREAL;
END_VAR

rOut := rIn * 2.0;"""

DEFAULT_POUS = [
    {"name": "MAIN", "kind": "program", "code": DEFAULT_MAIN},
    {"name": "FB_Aux", "kind": "function_block", "code": DEFAULT_AUX},
]
DEFAULT_CODE = DEFAULT_MAIN


def kind_of(declaration: str) -> str:
    upper = declaration.upper()
    if upper.startswith("FUNCTION_BLOCK"):
        return "function_block"
    if upper.startswith("FUNCTION"):
        return "function"
    return "program"


def normalize_pous(pous: list[dict]) -> list[dict]:
    if not isinstance(pous, list) or not pous:
        raise ValueError("at least one POU is required")
    result = []
    seen = set()
    for item in pous:
        name = str(item.get("name", "")).strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
            raise ValueError(f"invalid POU name: {name!r}")
        if name.upper() in seen:
            raise ValueError(f"duplicate POU: {name}")
        code = str(item.get("code", "")).strip()
        declaration, implementation = split_st(code)
        result.append({
            "name": name,
            "kind": kind_of(declaration),
            "declaration": declaration,
            "implementation": implementation,
            "code": code,
        })
        seen.add(name.upper())
    if "MAIN" not in seen:
        raise ValueError("a MAIN program is required")
    return result


def split_st(code: str) -> tuple[str, str]:
    """Split a full POU into declaration (through END_VAR) and body."""
    upper = code.upper()
    index = upper.find("END_VAR")
    if index < 0:
        raise ValueError("the code must contain a VAR ... END_VAR block")
    declaration = code[: index + len("END_VAR")].strip()
    implementation = code[index + len("END_VAR") :].strip()
    if not implementation:
        raise ValueError("the code has no implementation after END_VAR")
    if "PROGRAM" not in declaration.upper() and "FUNCTION_BLOCK" not in declaration.upper():
        raise ValueError("the declaration must start with PROGRAM or FUNCTION_BLOCK")
    return declaration, implementation


def plc_state() -> int | None:
    import pyads

    try:
        connection = pyads.Connection(NETID, PLC_PORT)
        connection.open()
        try:
            return int(connection.read_state()[0])
        finally:
            connection.close()
    except Exception:
        return None


class Runner:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.busy = False
        self.error = ""
        self.message = "idle"
        self.project = PROJECT_NAME
        self.pous = [dict(item) for item in DEFAULT_POUS]

    def status(self) -> dict:
        with self.lock:
            state = plc_state()
            return {
                "ok": True,
                "busy": self.busy,
                "message": self.message,
                "error": self.error,
                "project": self.project,
                "pous": [dict(item) for item in self.pous],
                "plc_state": state,
                "running": state == 5,
            }

    def start(self, pous: list[dict]) -> tuple[bool, str]:
        with self.lock:
            if self.busy:
                return False, "a run is already in progress"
            try:
                normalized = normalize_pous(pous)
            except ValueError as exc:
                return False, str(exc)
            self.busy = True
            self.cancel.clear()
            self.error = ""
            self.message = "starting"
            self.pous = [
                {"name": item["name"], "kind": item["kind"], "code": item["code"]}
                for item in normalized
            ]
        thread = threading.Thread(target=self._run, args=(normalized,), daemon=True)
        thread.start()
        return True, "started"

    def _log(self, message: str) -> None:
        try:
            path = ROOT / "runtime" / "code_server.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{time.strftime('%H:%M:%S')} {message}\n")
        except Exception:
            pass

    def _abort_if_cancelled(self) -> bool:
        if not self.cancel.is_set():
            return False
        self._set(message="stopped")
        return True

    def _run(self, pous: list[dict]) -> None:
        session = None
        try:
            self._set(message="starting thread")
            self._log("thread start")
            from automation.services.session import AutomationSession

            session = AutomationSession()
            if self._abort_if_cancelled():
                return
            self._set(message="creating project")
            self._log("create project")
            session.create(PROJECT_NAME, template=str(TEMPLATE))
            if self._abort_if_cancelled():
                return
            for item in pous:
                self._set(message=f"writing {item['name']}")
                self._log(f"write {item['name']} ({item['kind']})")
                session.write_pou(
                    item["name"],
                    item["declaration"],
                    item["implementation"],
                    item["kind"],
                )
                if self._abort_if_cancelled():
                    return
            self._set(message="building")
            self._log("build")
            build = session.build()
            if not build["ok"]:
                raise RuntimeError(f"build failed: {build.get('errors')}")
            if self._abort_if_cancelled():
                return
            self._set(message="activating")
            self._log("activate")
            session.activate_with_retry()
            if self._abort_if_cancelled():
                self._write_stop()
                return
            self._set(message="running", error="")
            self._log("running")
        except Exception as exc:
            self._set(message="error", error=f"{type(exc).__name__}: {exc}")
            self._log(f"error: {type(exc).__name__}: {exc}")
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
            with self.lock:
                self.busy = False

    def _set(self, *, message: str, error: str | None = None) -> None:
        with self.lock:
            self.message = message
            if error is not None:
                self.error = error

    def _write_stop(self) -> None:
        import pyads

        connection = pyads.Connection(NETID, PLC_PORT)
        connection.open()
        try:
            connection.write_control(pyads.ADSSTATE_STOP, 0, 0, pyads.PLCTYPE_BOOL)
        finally:
            connection.close()

    def stop(self) -> tuple[bool, str]:
        self.cancel.set()
        try:
            self._write_stop()
            self._set(message="stopped", error="")
            return True, "stopped"
        except Exception as exc:
            self._set(message="stop failed", error=str(exc))
            return False, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="TwinCAT panel code runner")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--http-port", type=int, default=8767)
    args = parser.parse_args()
    runner = Runner()

    class Handler(BaseHTTPRequestHandler):
        def _json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _empty(self) -> None:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._empty()

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path in {"/status", "/health"}:
                self._json(runner.status())
                return
            self._json({"ok": False, "error": "not found"}, 404)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/stop":
                ok, message = runner.stop()
                self._json({"ok": ok, "message": message}, 200 if ok else 500)
                return
            if parsed.path != "/run":
                self._json({"ok": False, "error": "not found"}, 404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                pous = payload.get("pous")
                if not pous:
                    code = str(payload.get("code", "")).strip()
                    if not code:
                        raise ValueError("pous or code is required")
                    pous = [{"name": "MAIN", "kind": "program", "code": code}]
                if not isinstance(pous, list):
                    raise ValueError("pous must be a list")
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
                return
            ok, message = runner.start(pous)
            self._json({"ok": ok, "message": message}, 202 if ok else 409)

        def log_message(self, fmt: str, *values: object) -> None:
            return

    server = ThreadingHTTPServer((args.host, args.http_port), Handler)
    print(f"TwinCAT code runner: http://{args.host}:{args.http_port}/status")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
