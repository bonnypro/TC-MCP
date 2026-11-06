# LoadControl simulation

See also: [MC_ExternalSetpointGenerator equivalent](mc3_external_setpoint.md) for the
MC3 axis implementation with the same 0 → 1000 → hold → 0 waveform.

## What is implemented

`automation/examples/loadcontrol.py` contains:

- `FB_LoadControl`: a deterministic PLC force-loop state machine
- `MAIN`: inputs `bStart` / `bReset`, output aliases, and one FB instance
- `tc_run_loadcontrol`: create, validate, build, activate, ADS test, report

State machine:

```text
0  IDLE
10 PRESSURIZE   actual += 1000 lb/s, cycle = 10 ms
20 HOLD         actual = 1000 lb, hold = 1 s
30 RELEASE      actual -= 2000 lb/s
40 COMPLETE
```

Test result on the local UM simulation:

```text
max_actual_lbs : 1000.0
hold_elapsed_s : 1.01
final_actual_lbs: 0.0
complete       : true
release_seen   : true
```

## What it is not

This is **not** a physical pressure test:

- no TF5560 hydraulic package
- no servo drive, pump, valve, or actuator
- no analog force sensor
- no pressure transducer
- no safety PLC or E-stop
- no real-time IPC
- no calibration or unit conversion to physical lbs

`rActualLbs` is computed inside the PLC. It is a software plant model for
testing sequencing, timing, ADS access, and report generation.

## ADS symbols

```text
MAIN.bStart
MAIN.bReset
MAIN.bPressurize
MAIN.bRelease
MAIN.bComplete
MAIN.nState
MAIN.rActualLbs
MAIN.rHoldElapsed
MAIN.fbLoadControl
```

## How to run

MCP:

```text
tc_run_loadcontrol { setpoint_lbs: 1000, hold_seconds: 1.0, timeout_s: 30 }
tc_ads_read { symbol: "MAIN.rActualLbs", type: "REAL" }
tc_close
```

Direct Python:

```powershell
$env:PYTHONPATH='.'
python runtime\run_loadcontrol_test.py
```

## Real-hardware path (not enabled)

Before any physical action, add:

1. A real force sensor input with scaling and plausibility checks.
2. A pressure/force limit independent of the control loop.
3. A watchdog and communication-loss safe state.
4. E-stop, guard, and safety-rated output isolation.
5. Servo torque/velocity limits or a hydraulic pressure relief valve.
6. A slow, low-force commissioning procedure on a safe test coupon.
7. Human approval and a physical risk assessment.

The current MCP must not be pointed at a real target. It enforces local ADS and
local activation only.
