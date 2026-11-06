"""Local ADS scope stream for the DSH live panel.

Samples one or more PLC symbols at a fixed interval and serves a rolling
buffer over HTTP for the Web UI plugin (and any other local client).

    python scripts/scope_stream.py --symbols "position=MAIN.Mc3SlaveAxis.McToPlc.Act.Position:LREAL,force=MAIN.rForce:LREAL"

Endpoints:
    GET /scope?after=<seq>&window=<n>   JSON samples + series metadata
    GET /health                         connection state
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class Store:
    def __init__(self, maxlen: int = 4000) -> None:
        self.lock = threading.Lock()
        self.samples: deque[dict] = deque(maxlen=maxlen)
        self.seq = 0
        self.connected = False
        self.error = ""
        self.series: list[dict] = []

    def add(self, values: list[float]) -> None:
        with self.lock:
            self.seq += 1
            self.samples.append({"seq": self.seq, "t": round(time.time(), 4), "v": values})

    def snapshot(self, after: int, window: int) -> dict:
        with self.lock:
            samples = [item for item in self.samples if item["seq"] > after]
            if len(samples) > window:
                samples = samples[-window:]
            return {
                "ok": not self.error,
                "connected": self.connected,
                "error": self.error,
                "series": self.series,
                "seq": self.seq,
                "samples": samples,
            }


def parse_symbols(raw: str) -> list[tuple[str, str, str]]:
    result = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        left, _, kind = item.partition(":")
        name, _, symbol = left.partition("=")
        if not symbol:
            raise ValueError(f"symbol must be name=SYMBOL[:TYPE]: {item!r}")
        result.append((name.strip() or symbol, symbol.strip(), (kind or "LREAL").upper()))
    if not result:
        raise ValueError("at least one symbol is required")
    return result


def sampler(store: Store, netid: str, port: int, symbols: list[tuple[str, str, str]], interval: float) -> None:
    import pyads

    connection = None
    while True:
        try:
            if connection is None:
                connection = pyads.Connection(netid, port)
                connection.open()
                store.series = [{"name": name, "symbol": symbol, "type": kind} for name, symbol, kind in symbols]
                store.connected = True
                store.error = ""
            values = []
            for _, symbol, kind in symbols:
                plc_type = getattr(pyads, f"PLCTYPE_{kind}", pyads.PLCTYPE_LREAL)
                value = connection.read_by_name(symbol, plc_type)
                values.append(float(value))
            store.add(values)
            time.sleep(max(0.005, interval))
        except Exception as exc:
            store.connected = False
            store.error = str(exc)
            try:
                if connection is not None:
                    connection.close()
            except Exception:
                pass
            connection = None
            time.sleep(2.0)


def main() -> int:
    parser = argparse.ArgumentParser(description="ADS scope stream")
    parser.add_argument("--netid", default="127.0.0.1.1.1")
    parser.add_argument("--port", type=int, default=851)
    parser.add_argument("--http-port", type=int, default=8766)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--interval", type=float, default=0.05)
    parser.add_argument(
        "--symbols",
        default="position=MAIN.Mc3SlaveAxis.McToPlc.Act.Position:LREAL",
        help="name=SYMBOL[:TYPE], comma separated",
    )
    args = parser.parse_args()
    symbols = parse_symbols(args.symbols)
    store = Store()
    store.series = [{"name": name, "symbol": symbol, "type": kind} for name, symbol, kind in symbols]
    thread = threading.Thread(
        target=sampler,
        args=(store, args.netid, args.port, symbols, args.interval),
        daemon=True,
    )
    thread.start()

    class Handler(BaseHTTPRequestHandler):
        def _json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._json({"ok": not store.error, "connected": store.connected, "error": store.error})
                return
            if parsed.path == "/scope":
                query = parse_qs(parsed.query)
                try:
                    after = int(query.get("after", ["0"])[0])
                    window = int(query.get("window", ["600"])[0])
                except ValueError:
                    after, window = 0, 600
                self._json(store.snapshot(after, max(10, min(window, 4000))))
                return
            self._json({"ok": False, "error": "not found"}, 404)

        def log_message(self, fmt: str, *values: object) -> None:
            return

    server = ThreadingHTTPServer((args.host, args.http_port), Handler)
    print(f"ADS scope: http://{args.host}:{args.http_port}/scope  ({args.netid}:{args.port}, {args.interval*1000:.0f} ms)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
