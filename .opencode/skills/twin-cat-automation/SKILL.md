---
name: twin-cat-automation
description: Use the tc-auto MCP to create, edit, build, activate, and test local TwinCAT simulation projects. Use for real XAE/ADS operations that are restricted to the local UM simulation.
---

# TC-AUTO MCP

`tc-auto` is the write-capable automation MCP. It is separate from the read-only
`tc-mcp` knowledge server.

## Safety

- Only `templates/` sources may be copied.
- Only `127.0.0.1.1.1:851` local simulation is allowed.
- One XAE/ADS session at a time.
- No real hardware, remote target, TwinCAT safety project, or physical pressure
  action.
- Always call `tc_close` when done.
- The LoadControl example is a simulated PLC force loop, not a real hydraulic
  test.

## Normal flow

```text
tc_status / tc_revision
tc_create
tc_write_dut / tc_write_gvl / tc_write_pou
tc_plc_add_library / tc_plc_list_libraries
tc_project_info / tc_project_tree
tc_build
tc_verify_symbol
tc_activate
tc_ads_write / tc_ads_read / tc_ads_wait
tc_test_run
tc_snapshot / tc_restore
tc_archive_export
tc_close
```

Use `tc_build` diagnostics before editing again. Use `tc_snapshot` before risky
changes and `expected_revision` on writes. Use `tc_archive_export` to produce a
portable `.tnzip`/`.tpzip` archive for handoff.

## LoadControl flow

```text
tc_run_loadcontrol { setpoint_lbs: 1000, hold_seconds: 1.0 }
tc_ads_read { symbol: "MAIN.rActualLbs", type: "REAL" }
tc_close
```

For a declarative test, use `tc_test_run` with a plan of build/activate/write/
wait/assert/snapshot steps. Teardown always runs.

Expected checks from `tc_run_loadcontrol`:

```text
complete
release_seen
reached_setpoint
hold_time
hold_time_bounded
released_to_zero
```

If a call fails, read the error text before retrying. Do not open a second XAE
or a second MCP session. Do not bypass `tc_validate_project` or
`tc_verify_symbol` when changing PLC code.

## Knowledge

Use `tc-mcp.search` for official Beckhoff PDF facts. Use `tc-auto` only for
local project and simulation operations.
