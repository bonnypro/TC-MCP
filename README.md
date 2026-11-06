# TC-MCP — TwinCAT 3 MCP servers (OpenCode edition)

This branch is the OpenCode integration for TC-MCP:

- `mcp_server.py` — read-only knowledge server over the local official PDF
  store (`search`, `get_document`, `status`).
- `automation_mcp_server.py` — `tc-auto`, a local XAE/ADS automation server
  (project create/build/activate, PLC source, ADS reads/writes, snapshots,
  load-control example).
- `.opencode/` + `opencode.jsonc` — OpenCode commands and skills
  (`/tc-doctor`, `/tc-search`, `/tc-ingest`, `/tc-update`, `/tc-clean`, ...).

The automation layer is a small ports-and-adapters package under `automation/`
(Python 3.15, pywin32 + pyads). It owns one local XAE instance and one ADS
session at a time and targets the local UM simulation
(`127.0.0.1.1.1:851`).

## Requirements

- TwinCAT 3.1.4026.27 (XAE/XAR)
- Python 3.15 with `pypdf`, `pywin32`, `pyads`
- Node.js 26 (only for the optional browser/CDP helpers)
- A valid TwinCAT license for the packages you use (MC3 needs TC3 MC3 Base/Axis)

## Quick start

```powershell
python -m pip install --user -r requirements.txt
python -m app.cli doctor
python -m app.cli status
python -m app.cli search "MC_LoadControl"
```

Start the servers:

```powershell
python mcp_server.py
python automation_mcp_server.py
```

Or open the workspace in OpenCode and use the `/tc-*` commands.

## Layout

```text
app/                      knowledge base (PDF store, SQLite FTS5, CLI)
automation/adapters/      XAE COM, ADS (pyads), dry-run, XML validation
automation/core/          project graph/model/config
automation/ports/         XAE/ADS/validator interfaces
automation/services/      session, runner, licensing, revisions, test plans
automation/examples/      load-control example
config/                   sources, tool profiles, automation config
templates/minimal/        small TwinCAT project used by the runner
.opencode/                OpenCode commands/skills
opencode.jsonc            OpenCode MCP configuration
mcp_server.py             knowledge MCP server
automation_mcp_server.py  tc-auto MCP server
```

## Scope

- Local UM simulation only; hardware activation stays manual.
- No safety-project writes.
- One XAE/one ADS session at a time.

## License

MIT — see `LICENSE`. Third-party notices are in `THIRD_PARTY_NOTICES.md`.
