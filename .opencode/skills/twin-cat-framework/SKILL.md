---
name: twin-cat-framework
description: Operate the local TC-MCP TwinCAT MC3 knowledge and MCP framework in F1. Use for doctor, status, ingest, search, update, and framework maintenance.
---

# TC-MCP framework

This project is in **F2**: knowledge base + read-only `tc-mcp` + local-simulation
`tc-auto` automation MCP.

`tc-auto` may create, build, activate, and test local simulation projects. Real
hardware remains out of scope unless a separate human approval exists.

Read `AGENTS.md` first. The local truth is:

- `app/` - Python CLI, PDF ingestion, source checks
- `mcp_server.py` - read-only TC-MCP knowledge MCP
- `automation_mcp_server.py` - tc-auto XAE/ADS MCP
- `automation/` - ports, adapters, session, validator, LoadControl example
- `templates/` - local project templates; source for copies
- `data/knowledge.sqlite` - write DB, opened read-only by MCP
- `data/pdfs/` - official PDF store; `/tc-update` refreshes it
- `runtime/` - generated projects, sessions, locks, audit log
- `updates/` - manual update/ingest reports
- `scripts/` - PowerShell entry points and Edge CDP helper
- Official PDFs come from `data/pdfs/`; no web crawl

Rules:

- Standard library first. Do not add a dependency without a concrete need.
- Do not install browsers, Playwright, Docker, PostgreSQL, or local models.
- GitHub only through Edge + webVPN.
- `/tc-update` is manual; there is no scheduled task.
- Every technical answer must cite a local document, URL, or code path.
- `tc-mcp` is read-only with respect to TwinCAT; `tc-auto` is local-simulation
  only. Neither may operate real hardware or a safety project.

Useful commands:

```powershell
python -m app.cli doctor
python -m app.cli status
python -m app.cli search "MC_Power"
pwsh -NoProfile -File scripts/update.ps1 -Scope all
```
