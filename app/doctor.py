from __future__ import annotations

import importlib.metadata
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


def _which(name: str) -> str | None:
    return shutil.which(name)


def _version(cmd: list[str], timeout: int = 10) -> str:
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) else ""
    except Exception as exc:
        return f"ERROR: {exc}"


def _pkg_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


def _check_fts5() -> bool:
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        conn.close()
        return True
    except Exception:
        return False


def _check_twincat() -> tuple[bool, str]:
    shell = Path(r"C:\Program Files\Beckhoff\TcXaeShell\Common7\IDE\TcXaeShell.exe")
    base = Path(r"C:\Program Files (x86)\Beckhoff\TwinCAT\3.1")
    if shell.exists() or base.exists():
        return True, str(shell if shell.exists() else base)
    return False, "TwinCAT XAE Shell not found"


def run() -> str:
    lines: list[str] = []
    add = lines.append

    add("TC-MCP - F1 Doctor")
    add("=" * 36)
    add(f"Python       : {sys.version.split()[0]}  ({sys.executable})")
    add(f"SQLite       : {sqlite3.sqlite_version}")
    add(f"FTS5         : {'OK' if _check_fts5() else 'MISSING'}")
    add(f"pypdf        : {_pkg_version('pypdf')}")
    add(f"PyMuPDF      : {_pkg_version('pymupdf')}")

    node = _which("node")
    add(f"Node.js      : {_version([node, '--version']) if node else 'NOT FOUND'}  ({node or '-'})")
    npm = _which("npm.cmd") or _which("npm")
    add(f"npm          : {_version([npm, '--version']) if npm else 'NOT FOUND'}  ({npm or '-'})")

    edge_paths = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ]
    edge = next((p for p in edge_paths if p.exists()), None)
    add(f"Edge         : {'OK' if edge else 'NOT FOUND'}  ({edge or '-'})")

    seven = _which("7z") or _which("7za")
    if not seven:
        for candidate in (Path(r"C:\Program Files\7-Zip\7z.exe"), Path(r"C:\Program Files (x86)\7-Zip\7z.exe")):
            if candidate.exists():
                seven = str(candidate)
                break
    add(f"7-Zip        : {_version([seven]) if seven else 'NOT FOUND'}  ({seven or '-'})")
    git = _which("git")
    add(f"Git          : {_version([git, '--version']) if git else 'NOT FOUND'}  ({git or '-'})")

    tcpkg = Path(r"C:\Program Files\Beckhoff\TcPkg\TcPkg.exe")
    add(f"TcPkg        : {'OK' if tcpkg.exists() else 'NOT FOUND'}  ({tcpkg})")
    tc_ok, tc_detail = _check_twincat()
    add(f"TwinCAT XAE  : {'OK' if tc_ok else 'NOT FOUND'}  ({tc_detail})")

    add("")
    add("Notes:")
    add("- Official Beckhoff downloads use direct network; GitHub uses Edge + webVPN.")
    add("- This framework never installs software automatically.")
    add("- F1: knowledge/MCP only; no TwinCAT project, axis, simulation, or hardware.")
    return "\n".join(lines)
