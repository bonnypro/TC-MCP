# Minimal TwinCAT project template

This is a minimal TwinCAT 3 solution used by the F2 simulation workflow.

- Solution: `Minimal.sln`
- System project: `TwinCATProject1/TwinCATProject1.tsproj`
- PLC project: `PLC1`
- Program: `MAIN` with `bReady : BOOL := TRUE`
- Task: `PlcTask` calls `MAIN`

The template was generated with TwinCAT XAE 4026.27 and references the standard
`Tc2_Standard`, `Tc2_System`, and `Tc3_Module` libraries from the local TwinCAT
installation. It is intentionally small and has no I/O, axes, safety project,
or hardware configuration.

The official compiled library binaries under `_Libraries/` are **not** shipped
in the public repository. Install TwinCAT XAE with the standard libraries before
building this template. The local `_Libraries` directory is ignored.

## Automation use

The runner copies the complete `templates/minimal` directory to
`runtime/runs/<timestamp>/<name>/`, writes the PLC declaration and
implementation through the XAE Automation Interface, builds for
`TwinCAT OS (x64)`, activates the local simulation runtime, and reads
`MAIN.bReady` through ADS.

Generated artifacts (`_Boot`, `_CompileInfo`, `*.tmc`, `*.bak`, trial-license
files) are not part of this template.
