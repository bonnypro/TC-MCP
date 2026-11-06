---
name: twin-cat-update
description: Run the PDF-first TwinCAT documentation update and review GitHub candidates.
---

# Manual update workflow

1. Run `pwsh -NoProfile -File scripts/update.ps1 -Scope all`.
2. Read the before/after source table (reachability and PDF update state).
3. Read `updates/latest/digest.md`.
4. Compare `data/pdf_versions.json` with the previous version.
5. Check the PDF/version update summary.
6. Classify GitHub candidates as absorb / evaluate / ignore.
7. Do not download or execute repositories before explicit approval.

The source registry is `config/sources.json`. The technical documentation source
is the official Beckhoff PDF download server. There is no InfoSys HTML crawler.

Official sources use direct HTTP. GitHub must use Edge + webVPN:

```powershell
pwsh -NoProfile -File scripts/edge-debug.ps1
```

If the Edge debug port is unavailable, report `PENDING` and ask the user to close Edge and run the helper. Do not use `git clone` or direct GitHub HTTP.

## PDF version tracking

- PDFs are stored in `data/pdfs/`.
- `data/pdf_versions.json` records filename, URL, size, SHA-256, ETag,
  Last-Modified, signature, download time, and extracted manual version.
- `/tc-update` compares ETag/Last-Modified/Content-Length and downloads only changed
  PDFs. Changed PDFs are re-extracted and re-indexed.
- Do not delete PDFs or the version file during routine updates.

## Credentials

There are no web-crawl credentials in the project. Technical sources are official
PDFs only. GitHub uses Edge + webVPN as a browser route, not stored credentials.

When the user approves absorption:

- Docs: ingest the approved text/PDF into `data/`.
- Samples/MCP/skills: download through Edge, review license/security, put under `vendor/`, then ingest only text that is safe to index.
- Record the decision in `data/audit.jsonl`.
- Never auto-execute downloaded code.
