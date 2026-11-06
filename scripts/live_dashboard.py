"""Local dashboard for TC-MCP live ("实操") mode.

Run with live mode enabled:

    $env:TC_MCP_MODE = "live"
    python scripts/live_dashboard.py --port 8765

Then open http://127.0.0.1:8765. The page polls events.jsonl and shows the
current phase and the exact PLC code being written into TwinCAT.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG = ROOT / "runtime" / "live" / "events.jsonl"

PAGE = """<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<title>TC-MCP 实操模式</title>
<style>
:root { color-scheme: dark; }
body { margin: 0; background: #10141a; color: #d8e1ea; font: 14px/1.5 Segoe UI, sans-serif; }
header { padding: 14px 20px; background: #161c24; border-bottom: 1px solid #263140;
         display: flex; gap: 14px; align-items: center; }
h1 { font-size: 17px; margin: 0; font-weight: 600; }
#status { width: 10px; height: 10px; border-radius: 50%; background: #5b6b7c; }
#status.live { background: #35d07f; box-shadow: 0 0 8px #35d07f; }
#mode { color: #8fa3b5; font-size: 12px; }
main { display: grid; grid-template-columns: 380px 1fr; height: calc(100vh - 56px); }
#timeline { overflow: auto; border-right: 1px solid #263140; padding: 8px; }
#code { overflow: auto; padding: 16px 20px; }
pre { margin: 0; white-space: pre-wrap; word-break: break-word; font: 13px/1.45 Consolas, monospace; }
.item { padding: 8px 10px; border-radius: 6px; cursor: pointer; margin-bottom: 4px; }
.item:hover { background: #1b2430; }
.item.active { background: #1f3a4d; }
.kind { color: #7fc4ff; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
.msg { color: #e8eef4; }
.ts { color: #6f8294; font-size: 11px; }
.empty { color: #6f8294; padding: 20px; }
</style>
<header>
  <div id="status"></div>
  <h1>TC-MCP 实操模式</h1>
  <div id="mode">silent / live</div>
</header>
<main>
  <div id="timeline"><div class="empty">等待事件…</div></div>
  <div id="code"><pre>// 选择一个步骤查看写入的 PLC 代码</pre></div>
</main>
<script>
let lastSeq = 0;
let selected = null;
const timeline = document.getElementById('timeline');
const code = document.getElementById('code');
const status = document.getElementById('status');
const mode = document.getElementById('mode');

function esc(value) {
  return String(value ?? '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
}

function show(item) {
  selected = item.seq;
  document.querySelectorAll('.item').forEach(el => el.classList.toggle('active', Number(el.dataset.seq) === selected));
  const d = item.data || {};
  let text = d.code;
  if (!text && (d.declaration || d.implementation)) {
    text = (d.declaration || '') + '\\n\\n' + (d.implementation || '');
  }
  if (!text) {
    text = JSON.stringify(d, null, 2);
  }
  code.innerHTML = '<pre>' + esc(text) + '</pre>';
}

function add(item) {
  const empty = timeline.querySelector('.empty');
  if (empty) empty.remove();
  const div = document.createElement('div');
  div.className = 'item';
  div.dataset.seq = item.seq;
  div.innerHTML = '<div class="kind">' + esc(item.kind) + '</div>' +
                  '<div class="msg">' + esc(item.message || '') + '</div>' +
                  '<div class="ts">' + esc(item.ts) + '</div>';
  div.onclick = () => show(item);
  timeline.appendChild(div);
  timeline.scrollTop = timeline.scrollHeight;
  if (item.data && item.data.code) show(item);
  else if (!selected) show(item);
}

async function poll() {
  try {
    const res = await fetch('/events?after=' + lastSeq, {cache: 'no-store'});
    if (res.ok) {
      const items = await res.json();
      for (const item of items) {
        lastSeq = Math.max(lastSeq, item.seq);
        add(item);
      }
      const live = items.length > 0 || (Date.now() - lastEventMs < 5000);
      if (items.length) lastEventMs = Date.now();
      status.className = live ? 'live' : '';
    }
  } catch (e) {}
  setTimeout(poll, 700);
}
let lastEventMs = 0;
mode.textContent = 'live 事件流';
poll();
</script>
</html>"""


def load_events(path: Path, after: int, limit: int = 500) -> list[dict]:
    if not path.is_file():
        return []
    items: list[dict] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if int(item.get("seq", 0)) > after:
                    items.append(item)
    except OSError:
        return []
    return items[-limit:]


def main() -> int:
    parser = argparse.ArgumentParser(description="TC-MCP live-mode dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    args = parser.parse_args()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                return
            if parsed.path == "/events":
                query = parse_qs(parsed.query)
                try:
                    after = int(query.get("after", ["0"])[0])
                except ValueError:
                    after = 0
                body = json.dumps(load_events(args.log, after), ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
                return
            if parsed.path == "/health":
                self._send(200, b'{"ok": true}', "application/json")
                return
            self._send(404, b"not found", "text/plain")

        def log_message(self, fmt: str, *values: object) -> None:
            return

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"TC-MCP live dashboard: http://{args.host}:{args.port}  (log: {args.log})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
