---
name: twin-cat-load-control
description: Domain rules for the servo + lead-screw + analog force-sensor load control. Use for local simulation and review; real hardware is not enabled.
---

# Servo load control

- Machine: servo motor + lead screw + analog force sensor.
- Current implementation is a **PLC-simulated force loop** in `automation/examples/loadcontrol.py`.
- No real hardware, actuator, force sensor, or safety-rated pressure path exists on this laptop.
- Use `tc_run_loadcontrol` for the local simulation test.
- Do not install TF5560. It is for hydraulic FluidPower, not this machine.
- Do not assume `MC_LoadControl` works on a standard servo axis.
  After F2, first check the MC3 axis `Load Control Type` / load-feedback options.
  If unavailable, use a PLC force loop.

Implemented simulation sequence:

```text
pressurize -> reach 1000 lb -> hold 1 s -> release -> complete
```

`FB_LoadControl` uses a deterministic simulated plant:

```text
state 10: pressure ramps at 1000 lb/s
state 20: actual = setpoint for 1 s
state 30: pressure ramps down at 2000 lb/s
state 40: complete
```

Preferred real-machine sequence (not enabled):

```text
fast approach -> slow approach -> contact detection -> force control -> hold -> retract
```

- Position/velocity mode is enough; torque mode is optional.
- Keep a torque limit as a safety limit.
- Simulated sensor model: `force = clamp((position - contact) * stiffness, 0, max_force)` plus filter/noise.
- Test: overshoot, steady-state error, contact detection, sensor fault, retract, force limit.
- No real hardware action without E-stop, limits, and human approval.
- Do not create TwinCAT axes in F1.
