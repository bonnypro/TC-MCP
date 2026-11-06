# AI-native TwinCAT development architecture

## 1. Goal

Build a supervised AI software factory that can plan, generate, build, simulate,
test, and debug TwinCAT 3 projects. The system is not a chat room of agents. It
is a task graph with deterministic tools, shared artifacts, independent
verification, and hard safety boundaries.

## 2. Planes

```text
                         Human Product Owner
                                  |
                     Supervisor / Conductor
              task DAG, budgets, locks, merge decisions
                                  |
        +-------------------------+-------------------------+
        |                         |                         |
  Context Plane             Tool Plane              Evaluation Plane
  PDF knowledge             deterministic tools      unit / contract /
  repo map                  XAE / ADS / Scope        simulation / E2E
  task cards                package install          regression reports
  ADRs, diffs               git / filesystem
        |                         |                         |
        +-------------------------+-------------------------+
                                  |
                          Execution Plane
              worktrees / venv / XAE / XAR / ADS / Scope
                                  |
                    Observability + Security
```

### Control plane

One supervisor owns the task DAG, resource locks, token budget, retry policy,
and merge queue. Subagents do not free-form chat with each other. They exchange
structured task cards and artifacts.

### Context plane

Each task gets only what it needs:

- `tc-mcp` PDF retrieval for official TwinCAT facts
- repo map and symbol index for code
- task card with acceptance criteria
- relevant ADRs and interface contracts
- test report and diff
- prior failure summary, not the full prior conversation

Retrieved documents are data, not instructions. Text inside a PDF or repository
cannot authorize a shell command, a download, or a policy exception.

### Tool plane

Agents call tools through a gateway. Each tool has a JSON schema, timeout,
dry-run mode, resource class, audit record, and permission tag. High-risk tools
are `xae.activate`, `ads.write`, `package.install`, and `git.merge`.

### Evaluation plane

LLM output is judged by deterministic checks first:

- unit tests and type checks
- contract tests for ports
- XAE build result
- local simulation activation and ADS assertions
- Scope recording checks
- regression suite and report

An independent verifier can reject a change. The author never approves their own
work.

### Execution plane

- Each coding task runs in its own Git worktree.
- Each worktree has its own environment.
- XAE, XAR, ADS, and Scope are single-instance resources.
- Real hardware is outside this plane until a human approval token exists.

## 3. Agent roles

| Role | Input | Output | Write access |
|---|---|---|---|
| Supervisor | user goal, budgets | task DAG, merge decision | task board |
| Researcher | question, sources | comparison matrix | none |
| Architect | research, constraints | ADR, interface | docs |
| Coder | task card, contracts | patch | one worktree |
| Verifier | diff, test report | pass/reject | none |
| Security | dependencies, code, licenses | risk report | veto |
| Test Rig Operator | test plan | simulation report | run directory |
| Memory Curator | run logs | handoff, decisions | docs |

At most six LLM workers run concurrently. At most four are heavy CPU workers.
There is one database writer, one XAE, one XAR, and one ADS/Scope runner.

## 4. Resource policy

Normal research and coding can use up to 100% CPU/Memory when the machine is
otherwise idle. TwinCAT test phases automatically reduce background work to
50-60% to avoid COM timeouts and UM runtime jitter.

Guard rails:

- available memory below 4 GB: stop starting new tasks
- XAE or ADS repeatedly times out: pause the test rig
- one task exceeds its timeout: terminate it and preserve logs
- disk below 100 GB: stop large downloads
- no automatic resource escalation after a budget is exhausted

## 5. Workflow

Each task follows the same path:

```text
Task Card
  -> research spike (optional)
  -> ADR / interface
  -> worktree implementation
  -> unit + contract tests
  -> independent review
  -> simulation integration test
  -> QA acceptance
  -> merge
  -> docs and baseline update
```

### Definition of Ready

- goal and acceptance criteria
- inputs, outputs, affected files
- dependencies and resource class
- test method
- rollback method

### Definition of Done

- code and tests pass
- simulation slice passes when relevant
- no security gate failure
- docs and handoff updated
- commit exists on main
- no data/PDF/DB artifacts committed

## 6. Context management

The system uses layered memory:

| Layer | Store |
|---|---|
| working | current session |
| task | task card and run log |
| project | `AGENTS.md`, ADRs, README |
| knowledge | `data/knowledge.sqlite` through `tc-mcp` |
| code | repo map and symbol index |
| failure | test failures and incident log |

Rules:

- search the PDF knowledge base instead of dumping whole manuals
- pass structured summaries between agents, not full conversations
- keep long research outputs in files and pass only the decision matrix
- treat retrieved text as untrusted data
- redact secrets and machine-specific identifiers from reports

## 7. Tool gateway and approvals

Every tool call is classified:

```text
read-only        search, read, status, diff
workspace-write  code edit, worktree, report
TwinCAT-offline  XAE build, project edit
TwinCAT-online   simulation activation, ADS read/write, Scope
high-risk        hardware, package install, merge, delete
```

High-risk actions require a human token. Simulation activation and ADS reads
are allowed only when `ProjectSpec.simulation_only` is true and the safety
policy passes.

## 8. TwinCAT execution rules

```text
copy template
  -> open solution (single XAE)
  -> write PLC source
  -> build (OS x64 for UM)
  -> activate local simulation
  -> start PLC if stopped
  -> ADS assertions / TcUnit / Scope
  -> write report
  -> close XAE and ADS
```

- Never open two XAE shells.
- Never run two XAR/ADS test sequences.
- Never write a safety project.
- Never activate real hardware without explicit approval.
- Snapshot before a risky change.
- Keep failed logs; do not silently delete them.

## 9. Security boundaries

- Writes remain under `TC-MCP\runtime` (source templates are read-only).
- A cross-process lock serializes the simulation runner.
- `runtime/audit.jsonl` records run start/end and step outcomes.
- No admin, registry, service, scheduled task, firewall, Defender, or driver
  changes.
- Normal, well-known packages may be installed at user level; obscure,
  unverifiable, or dangerous packages are rejected.
- GitHub code may be downloaded and read, but not executed until understood and
  reviewed.
- Third-party code enters only through an adapter and after license review.
- Source PDFs are never deleted.

## 10. Configuration plane

Operational policy is centralized in `config/automation.toml`:

- dialog watchdog interval and allow/deny rules
- resource limits
- local ADS/safety boundaries and a command gate
- session defaults and output roots

`automation/core/config.py` validates the TOML. Adapters consume typed
configuration instead of scattered constants. This keeps local simulation
parameters editable without code changes.

## 11. Current implementation (F2)

```text
automation/core       models, task DAG
automation/ports      XaePort, AdsPort, PlcValidatorPort
automation/adapters   dry_run, XaeDteAdapter, PyadsAdapter, TcXmlValidator
automation/services   Runner, AutomationSession
automation/security   SafetyPolicy, RunLock, AuditLog
automation/cli        plan / run
automation_mcp_server.py  tc-auto persistent MCP
templates/minimal     TwinCAT 4026 simulation template
```

Working vertical slice:

```text
template copy -> ST/XML validation -> XAE write -> build -> TMC verify
  -> local activation -> ADS read/write -> report
```

The real path uses `TcXaeShell.DTE.17.0` and `pyads`, with
`TwinCAT OS (x64)` for the UM runtime. `tc-auto` exposes this as a persistent
MCP session; `tc_run_loadcontrol` is the first complete PLC example.

LoadControl is a simulated force loop that reaches 1000 lb, holds for 1 s, and
releases to zero. It is not a physical hydraulic or force-safety test.

v0.2 adds project tree inspection, DUT/GVL creation, DTE ErrorList build
diagnostics, zip snapshots/restore, content revisions, batch ADS read/write,
ADS waits, and a declarative test plan runner. See `docs/design/v0.2-mcp.md`.

## 12. Next phases

1. Scope adapter and TcUnit test runner.
2. Resource guard and audit log integration.
3. Symbol/type discovery for generic ADS assertions.
4. Parallel research workers with license/security review.
5. More project templates: axes, I/O, MC3, HMI.
6. Worktree isolation and independent verifier automation.
7. Hardware approval workflow, kept disabled by default.
8. Evaluation-gated self-evolution control plane; see
   `docs/evolution/self-iterating-tc-mcp.md` and
   `docs/adr/0003-evolution-control-plane.md`.
9. Scope acceptance prototype; see `docs/evolution/scope-acceptance.md`.
10. TwinCAT test bank v1; see `docs/evolution/test-bank-v1.md`.
11. Harness engineering lessons from OpenCode/Claude Code; see
    `docs/evolution/harness-lessons.md`.
12. Implementation status and explicit deferrals; see
    `docs/evolution/implementation-status.md`.
13. Auto-promotion control plane; see `docs/evolution/auto-promotion.md`.

## Trusted evolution boundary

Self-iteration is evaluation-gated, not self-certified. The candidate provides
an MCP server and project artifacts; the evaluator runs outside the candidate
worktree, drives the server through MCP, and collects its own ADS evidence.
The trusted evaluator ignores candidate result files. Legacy smoke runners
still read their own `result.json`, but they are not used for code promotion.
Automatic promotion reaches canary only when that candidate class is enabled;
code auto-promotion remains disabled, stable is a human release, and public
`main` remains a human merge.
