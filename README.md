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
automation/observability/ live event stream for 实操 mode
config/                   sources, tool profiles, automation config
templates/minimal/        small TwinCAT project used by the runner
scripts/live_dashboard.py local live-event dashboard
scripts/live_tui.py       terminal UI for the same live event stream
scripts/scope_stream.py   optional ADS scope HTTP stream
scripts/code_server.py    optional HTTP POU run/stop server
.opencode/                OpenCode commands/skills
opencode.jsonc            OpenCode MCP configuration
mcp_server.py             knowledge MCP server
automation_mcp_server.py  tc-auto MCP server
```

## Live mode (实操模式) / Silent mode (静默模式)

Silent mode is the default: no extra output and no event file.

To watch an automatic run in a browser:

```powershell
python scripts/live_dashboard.py --port 8765
$env:TC_MCP_MODE = "live"
python your_automation_script.py
```

Open <http://127.0.0.1:8765>. Every `write_pou` / `write_dut` / `write_gvl`
event carries the exact declaration and implementation before it is sent to
TwinCAT; `build`, `activate`, `ads_write`, retries, snapshots and archive
exports are shown as separate steps. Events are appended to
`runtime/live/events.jsonl` (`TC_MCP_LIVE_LOG` overrides the path).
`TC_MCP_LIVE_CODE=0` suppresses the console code preview.

Terminal UI:

```powershell
python scripts/live_tui.py
```

Keys: `↑/↓` select, `PgUp/PgDn` scroll code, `Tab` switch pane, `f` follow,
`q` quit.

## ADS scope and POU runner (optional)

`scripts/scope_stream.py` samples configured PLC symbols and serves a rolling
buffer over local HTTP; variables can be added or removed while it runs:

```powershell
python scripts/scope_stream.py --symbols "position=MAIN.Mc3SlaveAxis.McToPlc.Act.Position:LREAL,force=MAIN.rForce:LREAL"
```

`scripts/code_server.py` is a local HTTP endpoint used by external panels to
write, build and activate one or more POUs, and to stop the PLC:

```powershell
python scripts/code_server.py --http-port 8767
```

Both are plain standard-library servers; no extra Python packages beyond the
normal automation requirements.

## Scope

- Local UM simulation only; hardware activation stays manual.
- No safety-project writes.
- One XAE/one ADS session at a time.

## Process lifecycle

MCP servers install `scripts/mcp_lifecycle.py` at startup:

- a Windows kill-on-close job object keeps every child (including a
  `TcXaeShell` started by `tc-auto`) tied to the server process;
- a parent watchdog exits the server when OpenCode exits or is killed;
- normal stdin EOF still closes the `tc-auto` session.

So closing OpenCode does not leave `tc-auto`, `tc-mcp`, `mcp_launcher`, or
their XAE shells behind. The TwinCAT **runtime service** is deliberately not
touched; it is a system service, not an MCP child.

## License

MIT — see `LICENSE`. Third-party notices are in `THIRD_PARTY_NOTICES.md`.
