from __future__ import annotations

import argparse
from pathlib import Path

from . import db, doctor, ingest, pdf_versions, sources as source_registry, update_docs
from .config import DB_PATH, OFFICIAL_TEXT, ensure_dirs, load_json


def cmd_doctor(_: argparse.Namespace) -> int:
    print(doctor.run())
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    conn = db.connect()
    s = db.stats(conn)
    pending = db.pending_changes(conn)
    print("Knowledge base")
    print("=" * 36)
    print(f"DB       : {DB_PATH}")
    for key, value in s.items():
        print(f"{key:10}: {value}")
    print(f"pending  : {len(pending)}")
    last = conn.execute("SELECT MAX(fetched_at) AS t FROM documents").fetchone()
    print(f"last doc : {last['t'] if last and last['t'] else '-'}")
    conn.close()
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    conn = db.connect()
    rows = db.search(conn, args.query, args.limit)
    conn.close()
    if not rows:
        print("No results.")
        return 1
    for i, row in enumerate(rows, 1):
        print(f"\n[{i}] {row['title']}  |  {row['section'] or '-'}")
        print(f"    {row['url']}")
        print(f"    {row['snippet']}")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    ensure_dirs()
    conn = db.connect()
    if args.path:
        path = Path(args.path)
        if path.is_dir():
            result = ingest.ingest_directory(conn, path, source_id=args.source)
        else:
            ok, reason = ingest.ingest_file(conn, path, source_id=args.source, url=args.url)
            result = {"seen": 1, "ingested": int(ok), "skipped": int(not ok and reason != "duplicate"), "duplicate": int(reason == "duplicate"), "errors": 0}
        label = str(path)
    else:
        print("Provide a path.")
        conn.close()
        return 2
    db.record_audit(conn, "ingest", f"{label}: {result}")
    conn.close()
    print(f"Ingest {label}: {result}")
    return 0


def cmd_sources(args: argparse.Namespace) -> int:
    ensure_dirs()
    registry = source_registry.load_registry()
    rows = source_registry.collect_status(registry, ping=not args.no_ping)
    print(source_registry.render_status(rows, title="Knowledge sources"))
    print(f"\nRegistry: {source_registry.REGISTRY_PATH}")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    ensure_dirs()
    registry = source_registry.load_registry()
    before = source_registry.collect_status(registry, ping=True)
    print(source_registry.render_status(before, title="Knowledge sources - before update"))
    print()

    digest = update_docs.run(args.scope)
    print(f"\nDigest: {digest}")

    results = load_json(digest.parent / "results.json", {})
    doc_results = results.get("docs", []) if isinstance(results, dict) else []
    after = source_registry.collect_status(registry, results=doc_results, ping=False)
    before_by_id = {row["id"]: row for row in before}
    for row in after:
        old = before_by_id.get(row["id"])
        if old:
            row["reachable"] = old["reachable"]
            row["http_status"] = old["http_status"]
            row["latency_ms"] = old["latency_ms"]
            row["error"] = row["error"] or old["error"]
    print()
    print(source_registry.render_status(after, title="Knowledge sources - update result"))

    if args.scope in {"all", "docs"}:
        conn = db.connect()
        try:
            documents = source_registry.flatten_documents(registry)
            by_id = {item["id"]: item for item in documents if item.get("url")}
            official = {"ingested": 0, "updated": 0, "unchanged": 0, "errors": 0}
            docs_dir = OFFICIAL_TEXT
            if docs_dir.exists():
                for text_file in sorted(docs_dir.glob("*.txt")):
                    source = by_id.get(text_file.stem)
                    if not source:
                        continue
                    try:
                        text = text_file.read_text(encoding="utf-8", errors="ignore")
                        digest_hash = db.sha256_text(text)
                        old = conn.execute(
                            "SELECT hash FROM documents WHERE url = ? ORDER BY id DESC LIMIT 1",
                            (source["url"],),
                        ).fetchone()
                        if old and old["hash"] == digest_hash:
                            official["unchanged"] += 1
                            continue
                        db.delete_documents_by_url(conn, source["url"])
                        ok, _ = ingest.ingest_file(
                            conn,
                            text_file,
                            source_id=source["id"],
                            version=str(source.get("version", "")),
                            url=source["url"],
                        )
                        official["ingested" if ok else "updated"] += 1
                    except Exception:
                        official["errors"] += 1
            print(f"official docs: {official}")
            db.set_meta(conn, "last_ingest", db.utc_now())

        finally:
            conn.close()
        try:
            version_file = pdf_versions.record()
            print(f"PDF versions: {version_file}")
        except Exception as exc:
            print(f"PDF version record failed: {exc}")
            return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="twin-cat-agent", description="TwinCAT 3 knowledge agent")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="check local environment")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("status", help="knowledge base status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("search", help="search local knowledge base")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("sources", help="show source registry, reachability, and update state")
    p.add_argument("--no-ping", action="store_true", help="do not probe remote hosts")
    p.set_defaults(func=cmd_sources)

    p = sub.add_parser("ingest", help="ingest a local file/folder")
    p.add_argument("path", nargs="?")
    p.add_argument("--source", default="local")
    p.add_argument("--url", default="", help="canonical source URL for a single file")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("update", help="check official docs and GitHub")
    p.add_argument("--scope", choices=["all", "docs", "github"], default="all")
    p.set_defaults(func=cmd_update)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
