"""Local control server for the DSH TwinCAT side panel.

Accepts a complete ``PROGRAM MAIN`` body from the panel, writes it into a copy
of ``templates/minimal``, builds and activates it.  Run/Stop are asynchronous
so the browser can poll ``/status`` while TwinCAT is restarting.

    python scripts/code_server.py --http-port 8767
"""

from __future__ import annotations

import argparse
import json
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

DEFAULT_CODE = """PROGRAM MAIN
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
        self.busy = False
        self.error = ""
        self.message = "idle"
        self.code = DEFAULT_CODE

    def status(self) -> dict:
        with self.lock:
            state = plc_state()
            return {
                "ok": True,
                "busy": self.busy,
                "message": self.message,
                "error": self.error,
                "code": self.code,
                "plc_state": state,
                "running": state == 5,
            }

    def start(self, code: str) -> tuple[bool, str]:
        with self.lock:
            if self.busy:
                return False, "a run is already in progress"
            try:
                split_st(code)
            except ValueError as exc:
                return False, str(exc)
            self.busy = True
            self.error = ""
            self.message = "starting"
            self.code = code
        thread = threading.Thread(target=self._run, args=(code,), daemon=True)
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

    def _run(self, code: str) -> None:
        session = None
        try:
            self._set(message="starting thread")
            self._log("thread start")
            from automation.services.session import AutomationSession

            session = AutomationSession()
            self._set(message="creating project")
            self._log("create project")
            session.create("panel-run", template=str(TEMPLATE))
            declaration, implementation = split_st(code)
            self._set(message="writing PLC code")
            self._log("write POU")
            session.write_pou("MAIN", declaration, implementation, "program")
            self._set(message="building")
            self._log("build")
            build = session.build()
            if not build["ok"]:
                raise RuntimeError(f"build failed: {build.get('errors')}")
            self._set(message="activating")
            self._log("activate")
            session.activate_with_retry()
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

    def stop(self) -> tuple[bool, str]:
        import pyads

        try:
            connection = pyads.Connection(NETID, PLC_PORT)
            connection.open()
            try:
                connection.write_control(pyads.ADSSTATE_STOP, 0, 0, pyads.PLCTYPE_BOOL)
            finally:
                connection.close()
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
                code = str(payload.get("code", "")).strip()
                if not code:
                    raise ValueError("code is required")
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
                return
            ok, message = runner.start(code)
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
