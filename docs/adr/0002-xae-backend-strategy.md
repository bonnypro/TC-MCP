# ADR 0002: XAE backend strategy

## Status

Proposed after the 2026-11-03 Standalone System Manager investigation.

## Context

TC-AUTO currently uses `TcXaeShell.DTE.17.0` for project creation, PLC authoring,
build, activation, and diagnostics. TwinCAT 4026 also provides
`TcSysManagerRM` / Standalone System Manager, which can operate a `.tsproj`
without DTE.

The question is whether the backend should be selected by "headless
feasibility":

> Standalone when possible, DTE when headless is difficult, plus screenshots
> and mouse clicks for dialogs.

Local probes and official documentation show that this is the wrong axis.
Capability is operation-specific, and the two backends are not peers.

## Evidence

- Standalone can open a `.tsproj`, create a blank `.tsproj`, manipulate the
  system tree, create `TIRT` system tasks, save configuration, set target
  NetID, build/activate a project that already has a TMC, and restart the
  runtime.
- On this 4026.27 installation, Standalone could not:
  - access `NestedProject` (`E_FAIL`)
  - look up the nested PLC POUs path
  - create a PLC project from `"Standard PLC Template"`
  - import a `.tpzip` PLC archive
  - rebuild changed PLC source (TMC unchanged after a source edit)
  - expose `SaveProject` / `SaveAsArchive` as documented
  - read structured ErrorList details
- The official TE1000 v1.7.0 manual (2026-09-03) explicitly requires **a DTE
  connection** for "Creating PLC objects" (§6.5.5, p.229) and for reading/
  setting POU declaration/implementation (§6.5.6, p.232). POU/DUT/GVL creation
  is therefore not a documented standalone capability.
- Standalone requires XAE to be installed, must run on an STA thread, has no
  `TcAutomationSettings.SilentMode`, and can still show message boxes.
- DTE provides `.sln`/template creation, nested PLC editing, PLC compilation,
  `ErrorList` file/line diagnostics, `.tnzip` archive, and SilentMode, but it is
  an IDE process with modal-dialog and COM/STA risks.
- Real-world CI tools (Zeugwerk, Jenkins examples, TcUnit Runner) use
  TcXaeShell/DTE or `devenv`, not Standalone as a PLC authoring engine.

## Decision

Use **capability-scoped adapters**, not a global headless toggle.

1. **DTE is the primary engineering backend** for:
   - `.sln` creation/opening and template instantiation
   - nested PLC project access
   - POU/DUT/GVL editing
   - PLC build and structured `ErrorList` diagnostics
   - `.tnzip` archive and DTE-only commands
2. **Standalone is an optional system/activation backend** for:
   - `.tsproj` system tree edits
   - `TIRT` tasks and system configuration
   - target NetID
   - build/activate/restart of a prebuilt project, after a probe proves it is
     not a no-op
   - save/archive only where individually proven
3. **ADS is the runtime/test backend** for symbol access, state, waits, and
   tests; it is independent of both.
4. **UI automation is controlled, not blind**:
   - first use `SuppressUI` + `TcAutomationSettings.SilentMode`
   - detect owned modal windows with UI Automation/Win32
   - local sandbox may auto-handle reviewed dialogs (activation/restart/
     download/local target/trial license) only when explicitly enabled
   - unknown dialogs: cancel/close only; never OK/Yes
   - TwinSAFE/safety, OS/UAC/security, and remote targets remain human-only
   - screenshots are evidence; clicks use control identity, not coordinates or
     OCR; one action per dialog

See `docs/design/dialog-automation-policy.md`.

## Consequences

- The current DTE path remains the production path; no rushed replacement.
- A `CapabilityMatrix` and `tc_capabilities` / `tc_preflight` tools should be
  added.
- A thin `XaeStandaloneAdapter` can be added for proven system/task/activation
  operations, behind the same owner/session state machine.
- Every operation returns a typed capability decision: `PROVEN`, `LIKELY`,
  `UNVERIFIED`, `UNSUPPORTED`.
- No backend switch while the project is dirty.
- The UI assist layer is opt-in and supervised; it never silently dismisses a
  dialog.

## Required probes before Standalone PLC work

1. Typed-interface POU/DUT/GVL read/write, save, reopen.
2. Source edit -> `BuildTargetPlatform` -> TMC/boot artifact diff.
3. `CheckAllObjects` or equivalent PLC pass/fail.
4. `.tpzip` import and `.tszip` archive.
5. 32-bit vs 64-bit COM behavior.
6. Dialog behavior during build/activation.
7. Service/LocalSystem COM registration.

Until 1-3 pass, Standalone must not be the PLC authoring/build backend.
