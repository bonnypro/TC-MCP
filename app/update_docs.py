from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import OFFICIAL_TEXT, PDF_STORE, ROOT, STATE_PATH, UPDATES, ensure_dirs, load_json, save_json
from . import sources as source_registry

logging.getLogger("pypdf").setLevel(logging.ERROR)

USER_AGENT = "TwinCAT-MC3-Agent/0.1 (local research; contact: local user)"
TCPKG = Path(r"C:\Program Files\Beckhoff\TcPkg\TcPkg.exe")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _powershell_request(
    url: str, method: str, timeout: int
) -> tuple[int, dict[str, str], bytes]:
    """Windows schannel fallback for hosts that reset Python's TLS connection."""
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if sys.platform != "win32" or not powershell:
        raise RuntimeError("PowerShell fallback unavailable")

    escaped_url = url.replace("'", "''")
    out_path = ""
    if method.upper() == "GET":
        fd, out_path = tempfile.mkstemp(prefix="tc-mcp-", suffix=".bin", dir=str(ROOT / "tmp"))
        os.close(fd)
        # mkstemp already created the file; Invoke-WebRequest overwrites it.
        escaped_out = out_path.replace("'", "''")
        action = f"$r=Invoke-WebRequest -Uri '{escaped_url}' -Method Get -UseBasicParsing -TimeoutSec {timeout} -OutFile '{escaped_out}';"
    else:
        action = f"$r=Invoke-WebRequest -Uri '{escaped_url}' -Method Head -UseBasicParsing -TimeoutSec {timeout};"
    script = (
        "$ProgressPreference='SilentlyContinue';"
        "try{"
        f"{action}"
        "[pscustomobject]@{status=[int]$r.StatusCode;"
        "etag=[string]$r.Headers['ETag'];"
        "last_modified=[string]$r.Headers['Last-Modified'];"
        "content_length=[string]$r.Headers['Content-Length']}"
        "|ConvertTo-Json -Compress"
        "}catch{"
        "[pscustomobject]@{status=0;error=[string]$_.Exception.Message}"
        "|ConvertTo-Json -Compress"
        "}"
    )
    try:
        proc = subprocess.run(
            [powershell, "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout + 30,
            check=False,
        )
        payload = json.loads((proc.stdout or "").strip() or "{}")
        status = int(payload.get("status", 0) or 0)
        if status == 0:
            raise RuntimeError(payload.get("error", "PowerShell request failed"))
        headers = {
            "ETag": str(payload.get("etag", "")),
            "Last-Modified": str(payload.get("last_modified", "")),
            "Content-Length": str(payload.get("content_length", "")),
        }
        body = b""
        if method.upper() == "GET":
            body = Path(out_path).read_bytes()
        return status, headers, body
    finally:
        if out_path:
            Path(out_path).unlink(missing_ok=True)


def _request(url: str, method: str = "GET", timeout: int = 30) -> tuple[int, dict[str, str], bytes]:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    assert last_error is not None
    try:
        return _powershell_request(url, method, timeout)
    except Exception as fallback_error:
        raise last_error from fallback_error


def _fetch_pdf(source: dict, out_dir: Path, old: dict[str, Any]) -> dict[str, Any]:
    status, headers, _ = _request(source["url"], "HEAD", timeout=40)
    etag = headers.get("ETag", "")
    last_modified = headers.get("Last-Modified", "")
    length = headers.get("Content-Length", "")
    signature = f"{etag}|{last_modified}|{length}"
    old_sig = old.get("signature")
    changed = signature != old_sig
    result = {
        "id": source["id"],
        "source": source.get("source", ""),
        "kind": "pdf",
        "url": source["url"],
        "status": status,
        "changed": changed,
        "etag": etag,
        "last_modified": last_modified,
        "content_length": length,
        "signature": signature,
        "summary": "baseline" if not old else ("changed" if changed else "unchanged"),
        "text_chars": old.get("text_chars", 0) if not changed else 0,
    }
    if not changed:
        return result
    _, _, raw = _request(source["url"], "GET", timeout=120)
    digest = _sha256_bytes(raw)
    result["sha256"] = digest
    pdf_path = out_dir / "pdf" / f"{source['id']}.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(raw)
    stable_pdf = PDF_STORE / f"{source['id']}.pdf"
    stable_pdf.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdf_path, stable_pdf)
    try:
        import pymupdf  # type: ignore

        document = pymupdf.open(str(pdf_path))
        text = "\n\n".join((page.get_text() or "") for page in document)
        pages = document.page_count
        document.close()
    except Exception:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        pages = len(reader.pages)
    try:
        text_path = out_dir / "docs" / f"{source['id']}.txt"
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(text, encoding="utf-8")
        stable = OFFICIAL_TEXT / f"{source['id']}.txt"
        stable.write_text(text, encoding="utf-8")
        result["pages"] = pages
        result["text_chars"] = len(text)
        version_match = re.search(r"(?:Version|version)\s*[:=]?\s*([0-9]+(?:\.[0-9]+){1,3})", text[:20000])
        result["version"] = version_match.group(1) if version_match else "unknown"
    except Exception as exc:
        result["summary"] = f"changed; pdf parse failed: {exc}"
    return result


def _fetch_tcpkg(source: dict, old: dict[str, Any]) -> dict[str, Any]:
    package = source["package"]
    cmd = [str(TCPKG), "list", package, "-a", "--no-cache", "--as-json"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
        )
        raw = (proc.stdout or "").strip()
        start = raw.find("[")
        versions: list[str] = []
        if start >= 0:
            data = json.loads(raw[start:])
            for item in data:
                if isinstance(item, dict):
                    ver = item.get("Version") or item.get("version")
                    if ver:
                        versions.append(str(ver))
        if not versions:
            versions = re.findall(r"\b(\d+\.\d+\.\d+(?:\.\d+)?)\b", raw)[-10:]
        current = versions[-1] if versions else ""
        changed = current != old.get("current")
        return {
            "id": source["id"],
            "source": source.get("source", ""),
            "kind": "tcpkg",
            "package": package,
            "changed": changed,
            "current": current,
            "versions": versions[-10:],
            "status": proc.returncode,
            "summary": "baseline" if not old else ("changed" if changed else "unchanged"),
        }
    except Exception as exc:
        return {
            "id": source["id"],
            "source": source.get("source", ""),
            "kind": "tcpkg",
            "package": package,
            "changed": False,
            "error": str(exc),
            "summary": "error",
        }


def _run_github(out_dir: Path) -> dict[str, Any]:
    script = ROOT / "scripts" / "github_cdp.mjs"
    target = out_dir / "github.json"
    if not script.exists():
        return {"status": "PENDING", "reason": "github_cdp.mjs not found"}
    try:
        proc = subprocess.run(
            ["node", str(script), "--out", str(target)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        if target.exists():
            return json.loads(target.read_text(encoding="utf-8"))
        return {
            "status": "PENDING",
            "reason": (proc.stdout or proc.stderr or "no output").strip()[-1000:],
        }
    except Exception as exc:
        return {"status": "PENDING", "reason": str(exc)}


def _write_digest(out_dir: Path, doc_results: list[dict], github: dict[str, Any]) -> Path:
    lines = [
        f"# Update digest - {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## Official sources",
        "",
    ]
    changes = [r for r in doc_results if r.get("changed")]
    if not changes:
        lines.append("No changes detected.")
    for r in changes:
        lines.append(f"- **{r.get('id')}** ({r.get('kind')}): {r.get('summary')}")
        if r.get("url"):
            lines.append(f"  - {r['url']}")
        if r.get("package"):
            lines.append(f"  - package: `{r['package']}` current: `{r.get('current', '?')}`")
        if r.get("pages"):
            lines.append(f"  - pages: {r['pages']}, text chars: {r.get('text_chars', 0)}")
    lines += ["", "### All sources", ""]
    for r in doc_results:
        detail = r.get("summary", "")
        if r.get("error"):
            detail = f"ERROR: {r['error']}"
        lines.append(f"- `{r.get('id')}` ({r.get('kind')}): {detail}")
    lines += ["", "## GitHub", ""]
    status = github.get("status", "PENDING")
    lines.append(f"Status: **{status}**")
    if status != "ok":
        lines.append(f"Reason: {github.get('reason', '')}")
    else:
        for repo in github.get("repos", []):
            lines.append(
                f"- `{repo.get('repo')}` pushed: {repo.get('pushed_at', '?')} "
                f"stars: {repo.get('stars', '?')} license: {repo.get('license', '?')}"
            )
        for item in github.get("searches", []):
            lines.append(f"- search `{item.get('query')}`: {len(item.get('items', []))} result(s)")
    lines += [
        "",
        "## Decision",
        "",
        "Official PDFs are checked and re-indexed automatically. GitHub candidates",
        "are absorb / evaluate / ignore; their code is not downloaded until approved.",
        "",
    ]
    digest = out_dir / "digest.md"
    digest.write_text("\n".join(lines), encoding="utf-8")
    latest = UPDATES / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(digest, latest / "digest.md")
    if (out_dir / "github.json").exists():
        shutil.copy2(out_dir / "github.json", latest / "github.json")
    return digest


def run(scope: str = "all") -> Path:
    ensure_dirs()
    registry = source_registry.load_registry()
    documents = source_registry.flatten_documents(registry)
    state = load_json(STATE_PATH, {})
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_dir = UPDATES / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    if scope in {"all", "docs"}:
        for index, source in enumerate(documents, 1):
            old = state.get(source["id"], {})
            try:
                if source.get("kind") == "pdf":
                    result = _fetch_pdf(source, out_dir, old)
                elif source.get("kind") == "tcpkg":
                    result = _fetch_tcpkg(source, old)
                else:
                    result = {"id": source.get("id"), "changed": False, "summary": "unknown kind"}
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                result = {
                    "id": source.get("id"),
                    "source": source.get("source", ""),
                    "kind": source.get("kind"),
                    "url": source.get("url"),
                    "changed": False,
                    "summary": f"error: {exc}",
                }
            results.append(result)
            print(
                f"[{index}/{len(documents)}] {source.get('id')}: {result.get('summary', '')}",
                flush=True,
            )
            if result.get("changed"):
                # Keep only the state needed for change detection.
                state[source["id"]] = {
                    k: result.get(k)
                    for k in ("sha256", "etag", "last_modified", "signature", "current", "versions", "version")
                    if k in result
                }
            time.sleep(1.0)
        save_json(STATE_PATH, state)

    github: dict[str, Any] = {"status": "SKIPPED", "reason": "scope=docs"}
    if scope in {"all", "github"}:
        github = _run_github(out_dir)

    (out_dir / "results.json").write_text(
        json.dumps({"docs": results, "github": github}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    digest = _write_digest(out_dir, results, github)
    return digest


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "all")
