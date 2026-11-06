"""TUI viewer for TC-MCP live ("实操") mode.

Silent mode (静默模式) remains the default for automation.  Enable live mode and
run this viewer:

    $env:TC_MCP_MODE = "live"
    python scripts/live_tui.py

Keys: Up/Down or j/k select, PgUp/PgDn scroll code, Tab switch pane, f follow,
q quit.  It reads the same JSONL stream as the web dashboard, with no external
dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG = ROOT / "runtime" / "live" / "events.jsonl"

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
INV = "\x1b[7m"
CYAN = "\x1b[36m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"


def _wide(char: str) -> int:
    return 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1


def display_width(text: str) -> int:
    return sum(_wide(ch) for ch in text)


def clip(text: str, width: int) -> str:
    if width <= 0:
        return ""
    out = []
    used = 0
    for char in text:
        size = _wide(char)
        if used + size > width:
            break
        out.append(char)
        used += size
    return "".join(out)


def fit(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if display_width(text) <= width:
        return text + " " * (width - display_width(text))
    cut = clip(text, max(0, width - 1))
    return cut + "…" + " " * max(0, width - display_width(cut) - 1)


def enable_ansi() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        handle = ctypes.windll.kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


class EventTail:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.events: list[dict] = []

    def poll(self) -> bool:
        if not self.path.is_file():
            return False
        try:
            size = self.path.stat().st_size
            if size < self.offset:
                self.offset = 0
                self.events.clear()
            if size == self.offset:
                return False
            with self.path.open(encoding="utf-8") as handle:
                handle.seek(self.offset)
                chunk = handle.read()
                self.offset = handle.tell()
        except OSError:
            return False
        changed = False
        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                self.events.append(json.loads(line))
                changed = True
            except json.JSONDecodeError:
                continue
        return changed


def _code_of(event: dict) -> str:
    data = event.get("data") or {}
    code = data.get("code")
    if code:
        return str(code)
    if data.get("declaration") or data.get("implementation"):
        return str(data.get("declaration") or "") + "\n\n" + str(data.get("implementation") or "")
    return ""


def _summary(event: dict) -> str:
    data = event.get("data") or {}
    code = _code_of(event)
    message = str(event.get("message", ""))
    if code:
        return f"{message}  ({len(code.splitlines())} lines)"
    if message:
        return message
    if data:
        try:
            return json.dumps(data, ensure_ascii=False)[:160]
        except Exception:
            return str(data)[:160]
    return ""


def select_default(events: list[dict]) -> int:
    for index in range(len(events) - 1, -1, -1):
        if _code_of(events[index]):
            return index
    return max(0, len(events) - 1)


class App:
    def __init__(self, log: Path) -> None:
        self.tail = EventTail(log)
        self.log = log
        self.events = self.tail.events
        self.selected = -1
        self.follow = True
        self.focus = "timeline"
        self.code_scroll = 0
        self.left_scroll = 0
        self.last_draw = 0.0
        self.running = True

    def key(self, key: str) -> None:
        if key in {"q", "Q"}:
            self.running = False
        elif key == "tab":
            self.focus = "code" if self.focus == "timeline" else "timeline"
        elif key == "f":
            self.follow = not self.follow
            if self.follow and self.events:
                self.selected = select_default(self.events)
                self.code_scroll = 0
        elif key in {"up", "k"}:
            if self.focus == "code":
                self.code_scroll = max(0, self.code_scroll - 1)
            else:
                self.follow = False
                self.selected = max(0, self.selected - 1)
                self.code_scroll = 0
        elif key in {"down", "j"}:
            if self.focus == "code":
                self.code_scroll += 1
            else:
                self.selected = min(len(self.events) - 1, self.selected + 1)
                self.follow = self.selected >= len(self.events) - 1
                self.code_scroll = 0
        elif key == "pgup":
            if self.focus == "code":
                self.code_scroll = max(0, self.code_scroll - 10)
            else:
                self.selected = max(0, self.selected - 10)
                self.code_scroll = 0
        elif key == "pgdn":
            if self.focus == "code":
                self.code_scroll += 10
            else:
                self.selected = min(len(self.events) - 1, self.selected + 10)
                self.code_scroll = 0
        elif key == "home":
            if self.focus == "code":
                self.code_scroll = 0
            else:
                self.selected = 0
                self.follow = False
        elif key == "end":
            if self.focus == "code":
                self.code_scroll = 10_000
            else:
                self.selected = max(0, len(self.events) - 1)
                self.follow = True

    def draw(self) -> None:
        columns, rows = shutil.get_terminal_size((120, 40))
        rows = max(16, rows)
        left_w = max(34, min(56, int(columns * 0.36)))
        right_w = max(20, columns - left_w - 1)
        body_h = rows - 4

        timeline: list[str] = []
        if self.events:
            if self.selected < 0:
                self.selected = select_default(self.events)
            if self.selected >= len(self.events):
                self.selected = len(self.events) - 1
            if not self.follow and self.selected < self.left_scroll:
                self.left_scroll = self.selected
            if self.selected >= self.left_scroll + body_h:
                self.left_scroll = self.selected - body_h + 1
            self.left_scroll = max(0, min(self.left_scroll, max(0, len(self.events) - body_h)))
            for index in range(self.left_scroll, min(len(self.events), self.left_scroll + body_h)):
                event = self.events[index]
                stamp = str(event.get("ts", ""))[11:19]
                text = f"{stamp} {str(event.get('kind', '')).upper():<12} {_summary(event)}"
                line = fit(text, left_w)
                timeline.append((INV if index == self.selected else "") + line + RESET)
        else:
            timeline = [fit("等待事件…", left_w)]

        selected = self.events[self.selected] if self.events and 0 <= self.selected < len(self.events) else {}
        code = _code_of(selected)
        data = selected.get("data") or {}
        if code:
            title = f"{selected.get('kind', '')} {data.get('name', '')}".strip()
            code_lines = code.splitlines()
        else:
            title = f"{selected.get('kind', '')} {selected.get('message', '')}".strip()
            payload = {k: v for k, v in data.items() if k != "code"}
            code_lines = json.dumps(payload, ensure_ascii=False, indent=2).splitlines() if payload else ["(no code payload)"]

        if self.code_scroll > max(0, len(code_lines) - 1):
            self.code_scroll = max(0, len(code_lines) - 1)
        visible = code_lines[self.code_scroll : self.code_scroll + max(1, body_h - 2)]
        right: list[str] = [BOLD + fit(f" CODE  {title}", right_w) + RESET]
        for offset, line in enumerate(visible, start=self.code_scroll + 1):
            right.append(fit(f"{offset:>4}  {line}", right_w))
        while len(right) < body_h:
            right.append(" " * right_w)

        plain_header = (
            f"TC-MCP 实操模式   LIVE   {len(self.events)} events   "
            f"focus={self.focus}   follow={'on' if self.follow else 'off'}"
        )
        sub = DIM + fit(str(self.log), columns) + RESET
        footer = DIM + fit("↑/↓ 选择   PgUp/PgDn 滚动   Tab 切换   f 跟随   q 退出", columns) + RESET

        lines: list[str] = [BOLD + fit(plain_header, columns) + RESET, sub]
        for row in range(body_h):
            left = timeline[row] if row < len(timeline) else " " * left_w
            # ANSI in the selected row does not count toward display width.
            left_padding = " " * max(0, left_w - display_width(_strip_ansi(left)))
            right_line = right[row] if row < len(right) else " " * right_w
            lines.append(left + left_padding + DIM + "│" + RESET + right_line)
        lines.append(footer)

        sys.stdout.write("\x1b[H" + "\r\n".join(lines[:rows]) + "\x1b[J")
        sys.stdout.flush()


def _strip_ansi(text: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)


def read_key(timeout: float) -> str:
    if os.name == "nt":
        import msvcrt

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if msvcrt.kbhit():
                char = msvcrt.getwch()
                if char in {"\x00", "\xe0"}:
                    code = msvcrt.getwch()
                    return {
                        "H": "up",
                        "P": "down",
                        "I": "pgup",
                        "Q": "pgdn",
                        "G": "home",
                        "O": "end",
                    }.get(code, "")
                if char == "\t":
                    return "tab"
                return char
            time.sleep(0.03)
        return ""
    import select

    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if not ready:
        return ""
    char = sys.stdin.read(1)
    if char == "\x1b":
        seq = sys.stdin.read(2)
        return {"[A": "up", "[B": "down", "[5": "pgup", "[6": "pgdn", "[H": "home", "[F": "end"}.get(seq, "")
    return "\t" if char == "\t" else char


def once(log: Path) -> int:
    tail = EventTail(log)
    tail.poll()
    events = tail.events
    print("TC-MCP 实操模式 (snapshot)")
    print(f"log: {log}")
    print(f"events: {len(events)}")
    for index, event in enumerate(events):
        stamp = str(event.get("ts", ""))[11:19]
        print(f"[{index + 1}] {stamp} {event.get('kind', '')} {event.get('message', '')}")
    if events:
        selected = select_default(events)
        event = events[selected]
        code = _code_of(event)
        print("\n--- selected ---")
        print(f"{event.get('kind', '')} {event.get('message', '')}")
        if code:
            print(code)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="TC-MCP live TUI")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--once", action="store_true", help="print one snapshot and exit")
    args = parser.parse_args()

    if args.once or not sys.stdin.isatty() or not sys.stdout.isatty():
        return once(args.log)

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    enable_ansi()

    app = App(args.log)
    sys.stdout.write("\x1b[?1049h\x1b[?25l")
    try:
        while app.running:
            changed = app.tail.poll()
            if changed:
                app.events = app.tail.events
                if app.follow:
                    app.selected = select_default(app.events)
                    app.code_scroll = 0
            app.draw()
            key = read_key(0.25)
            if key:
                app.key(key)
    finally:
        sys.stdout.write("\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
