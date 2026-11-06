from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app import db
from app.config import DB_PATH

SERVER_NAME = "TC-MCP"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSIONS = {"2024-11-05", "2025-06-18", "2025-11-25", "2026-07-28"}


def _json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


class ToolServer:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def _conn(self):
        if not self.db_path.exists():
            raise FileNotFoundError(f"knowledge database not found: {self.db_path}")
        return db.connect_readonly(self.db_path)

    def search(self, arguments: dict) -> dict:
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise ValueError("query is required")
        limit = max(1, min(int(arguments.get("limit", 8)), 25))
        conn = self._conn()
        try:
            rows = db.search(conn, query, limit)
            return {
                "query": query,
                "count": len(rows),
                "results": [
                    {
                        "title": row.get("title"),
                        "section": row.get("section"),
                        "url": row.get("url"),
                        "snippet": row.get("snippet"),
                        "score": row.get("score"),
                    }
                    for row in rows
                ],
            }
        finally:
            conn.close()

    def get_document(self, arguments: dict) -> dict:
        doc_id = arguments.get("document_id")
        url = arguments.get("url")
        max_chars = max(500, min(int(arguments.get("max_chars", 6000)), 40000))
        if doc_id is None and not url:
            raise ValueError("document_id or url is required")
        conn = self._conn()
        try:
            if doc_id is not None:
                row = conn.execute(
                    "SELECT id, title, section, url, version, fetched_at, text FROM documents WHERE id = ?",
                    (int(doc_id),),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT id, title, section, url, version, fetched_at, text FROM documents WHERE url = ? "
                    "ORDER BY id DESC LIMIT 1",
                    (str(url),),
                ).fetchone()
            if not row:
                raise ValueError("document not found")
            text = row["text"] or ""
            return {
                "document_id": row["id"],
                "title": row["title"],
                "section": row["section"],
                "url": row["url"],
                "version": row["version"],
                "fetched_at": row["fetched_at"],
                "truncated": len(text) > max_chars,
                "text": text[:max_chars],
            }
        finally:
            conn.close()

    def status(self, _: dict) -> dict:
        if not self.db_path.exists():
            return {"db": str(self.db_path), "exists": False}
        conn = self._conn()
        try:
            return {
                "db": str(self.db_path),
                "exists": True,
                "stats": db.stats(conn),
                "last_ingest": db.get_meta(conn, "last_ingest", ""),
            }
        finally:
            conn.close()

    def call(self, name: str, arguments: dict):
        if name == "search":
            return self.search(arguments)
        if name == "get_document":
            return self.get_document(arguments)
        if name == "status":
            return self.status(arguments)
        raise ValueError(f"unknown tool: {name}")


TOOLS = [
    {
        "name": "search",
        "description": "Search the local TC-MCP knowledge base. Returns cited snippets from indexed official Beckhoff PDFs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms, e.g. MC_LoadControl or MC_Power"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 8},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_document",
        "description": "Read a document from the local knowledge base by document_id or URL. Text may be truncated.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "integer"},
                "url": {"type": "string"},
                "max_chars": {"type": "integer", "minimum": 500, "maximum": 40000, "default": 6000},
            },
        },
    },
    {
        "name": "status",
        "description": "Return TC-MCP knowledge-base status and counts.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _result(value) -> str:
    return _json_text({"content": [{"type": "text", "text": _json_text(value)}], "isError": False})


def _error(message: str) -> str:
    return _json_text({"content": [{"type": "text", "text": message}], "isError": True})


def handle(server: ToolServer, request: dict) -> dict | None:
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in PROTOCOL_VERSIONS else "2024-11-05"
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    if method == "notifications/initialized":
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}

    if method in {"server/discover", "server/discover/request"}:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {"tools": {"listChanged": False}},
            },
        }

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            value = server.call(str(name), arguments)
            return {"jsonrpc": "2.0", "id": request_id, "result": json.loads(_result(value))}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": request_id, "result": json.loads(_error(str(exc)))}

    if method in {"resources/list", "prompts/list"}:
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}

    if request_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def serve(db_path: Path) -> int:
    try:
        from scripts.mcp_lifecycle import install as install_lifecycle

        install_lifecycle()
    except Exception:
        pass
    server = ToolServer(db_path)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            sys.stderr.write(f"TC-MCP invalid json: {exc}\n")
            sys.stderr.flush()
            continue
        response = handle(server, request)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


def selftest(db_path: Path) -> int:
    server = ToolServer(db_path)
    print(_json_text(server.status({})))
    print(_json_text(server.search({"query": "MC_LoadControl", "limit": 3})))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="TC-MCP minimal knowledge server")
    parser.add_argument("--db", default=str(DB_PATH), help="read-only knowledge database")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    path = Path(args.db)
    if not path.exists() and DB_PATH.exists():
        path = DB_PATH
    if args.selftest:
        return selftest(path)
    return serve(path)


if __name__ == "__main__":
    raise SystemExit(main())
