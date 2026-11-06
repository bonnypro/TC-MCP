from __future__ import annotations

import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import (
    ROOT,
    SOURCES_PATH,
    load_json,
)

REGISTRY_PATH = SOURCES_PATH
USER_AGENT = "TC-MCP/0.2 (local knowledge source checker)"
TCPKG = Path(r"C:\Program Files\Beckhoff\TcPkg\TcPkg.exe")


def load_registry() -> dict[str, Any]:
    registry = load_json(REGISTRY_PATH, {})
    if not isinstance(registry, dict):
        return {"sources": []}
    registry.setdefault("sources", [])
    return registry


def enabled_sources(registry: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    registry = registry or load_registry()
    return [source for source in registry.get("sources", []) if source.get("enabled", True)]


def flatten_documents(registry: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return every tracked document together with its parent source."""
    registry = registry or load_registry()
    documents: list[dict[str, Any]] = []
    for source in enabled_sources(registry):
        for item in source.get("documents", []):
            document = dict(item)
            document.setdefault("source", source["id"])
            document["source_name"] = source.get("name", source["id"])
            documents.append(document)
    return documents


def _curl_status(url: str, timeout: int = 8) -> tuple[int | None, str]:
    """Windows schannel fallback when Python's TLS stack is reset by the CDN."""
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        return None, "curl not available"
    try:
        proc = subprocess.run(
            [
                curl,
                "--ssl-no-revoke",
                "-sS",
                "-I",
                "-L",
                "--max-time",
                str(max(3, timeout)),
                "-A",
                USER_AGENT,
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
            check=False,
        )
        for line in (proc.stdout or "").splitlines():
            if line.startswith("HTTP/"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    return int(parts[1]), ""
        return None, (proc.stderr or "no HTTP status").strip()[-200:]
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def _powershell_status(url: str, timeout: int = 8) -> tuple[int | None, str]:
    if os.name != "nt":
        return None, "PowerShell fallback unavailable"
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        return None, "PowerShell not available"
    escaped = url.replace("'", "''")
    script = (
        "$ProgressPreference='SilentlyContinue';"
        f"try{{$r=Invoke-WebRequest -Method Head -Uri '{escaped}' -UseBasicParsing "
        f"-TimeoutSec {max(3, timeout)}; $r.StatusCode}}"
        "catch{if($_.Exception.Response){[int]$_.Exception.Response.StatusCode}else{0}}"
    )
    try:
        proc = subprocess.run(
            [powershell, "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout + 8,
            check=False,
        )
        for token in (proc.stdout or "").split():
            if token.isdigit() and int(token) > 0:
                return int(token), ""
        return None, (proc.stderr or "no HTTP status").strip()[-200:]
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def _head(url: str, timeout: int = 8) -> tuple[int | None, str]:
    if not url:
        return None, "no URL"
    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, ""
    except urllib.error.HTTPError as exc:
        # A 403/405 still proves that the host answered. 405 means retry with GET.
        if exc.code == 405:
            return _get_status(url, timeout)
        return exc.code, ""
    except Exception as exc:  # noqa: BLE001 - report connectivity, never crash /update
        status, curl_error = _curl_status(url, timeout)
        if status is not None:
            return status, ""
        status, ps_error = _powershell_status(url, timeout)
        if status is not None:
            return status, ""
        return None, str(exc) or curl_error or ps_error


def _get_status(url: str, timeout: int = 8) -> tuple[int | None, str]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*", "Range": "bytes=0-0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, ""
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def _document_update_counts(
    source_id: str, results: list[dict[str, Any]] | None
) -> tuple[str, str]:
    if not results:
        return "-", ""
    relevant = [item for item in results if item.get("source") == source_id]
    if not relevant:
        return "-", ""
    changed = sum(1 for item in relevant if item.get("changed"))
    errors = sum(1 for item in relevant if item.get("error"))
    unchanged = len(relevant) - changed - errors
    detail = f"{changed} new, {unchanged} same"
    if errors:
        detail += f", {errors} err"
    return ("YES" if changed else "no"), detail


def collect_status(
    registry: dict[str, Any] | None = None,
    *,
    results: list[dict[str, Any]] | None = None,
    ping: bool = True,
    timeout: int = 8,
) -> list[dict[str, Any]]:
    registry = registry or load_registry()
    rows: list[dict[str, Any]] = []
    for index, source in enumerate(enabled_sources(registry), 1):
        kind = source.get("kind", "")
        document_count = len(source.get("documents", []))

        reachable: bool | None
        http_status: int | None = None
        error = ""
        elapsed_ms = 0
        if kind == "local":
            path = source.get("path")
            if source.get("id") == "tcpkg":
                reachable = TCPKG.exists()
                error = "" if reachable else "TcPkg.exe not found"
            else:
                reachable = True if not path else (ROOT / path).exists()
        elif source.get("check_mode") == "skip":
            reachable = None
            error = source.get("note", "skipped")
        elif ping:
            started = time.perf_counter()
            url = source.get("check_url") or source.get("url") or ""
            http_status, error = _head(url, timeout=int(source.get("timeout", timeout)))
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            reachable = bool(http_status and 200 <= http_status < 400)
            if http_status and not reachable and not error:
                error = f"HTTP {http_status}"
        else:
            reachable = None

        update, update_detail = _document_update_counts(source.get("id", ""), results)
        if source.get("id") == "tcpkg":
            if not results:
                update = "local"
            update_detail = "installed package versions"
        if source.get("id") == "beckhoff_github":
            update = "metadata"
            update_detail = "GitHub metadata check via /update --scope github"

        rows.append(
            {
                "index": index,
                "id": source.get("id", ""),
                "name": source.get("name", source.get("id", "")),
                "kind": kind,
                "category": source.get("category", ""),
                "auth": source.get("auth", "none"),
                "reachable": reachable,
                "http_status": http_status,
                "latency_ms": elapsed_ms,
                "documents": document_count,
                "update": update,
                "update_detail": update_detail,
                "error": error,
            }
        )
    return rows


def render_status(rows: list[dict[str, Any]], *, title: str = "Knowledge sources") -> str:
    headers = ["#", "Source", "Type", "Auth", "Reach", "ms", "Docs", "Update", "Detail"]
    table: list[list[str]] = []
    for row in rows:
        if row["reachable"] is True:
            reach = "YES" if not row["http_status"] else f"YES {row['http_status']}"
        elif row["reachable"] is False:
            reach = "NO"
        else:
            reach = "SKIP"
        detail = row["error"] or row["update_detail"]
        table.append(
            [
                str(row["index"]),
                row["name"],
                row["kind"],
                row["auth"],
                reach,
                str(row["latency_ms"] or ""),
                str(row["documents"] or ""),
                row["update"],
                detail,
            ]
        )

    widths = [len(header) for header in headers]
    for line in table:
        for idx, value in enumerate(line):
            widths[idx] = max(widths[idx], len(value))

    def border() -> str:
        return "+" + "+".join("-" * (width + 2) for width in widths) + "+"

    def line(values: list[str]) -> str:
        return "| " + " | ".join(value.ljust(widths[idx]) for idx, value in enumerate(values)) + " |"

    parts = [title, "=" * len(title), border(), line(headers), border()]
    parts.extend(line(values) for values in table)
    parts.append(border())
    return "\n".join(parts)
