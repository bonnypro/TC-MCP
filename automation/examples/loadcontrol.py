from __future__ import annotations

FB_DECLARATION = """FUNCTION_BLOCK FB_LoadControl
VAR_INPUT
    bStart : BOOL;
    bReset : BOOL;
    rSetpointLbs : REAL := 1000.0;
    rHoldSeconds : REAL := 1.0;
    rToleranceLbs : REAL := 10.0;
    rPressRateLbsPerSec : REAL := 1000.0;
    rReleaseRateLbsPerSec : REAL := 2000.0;
END_VAR
VAR_OUTPUT
    bPressurize : BOOL;
    bRelease : BOOL;
    bComplete : BOOL;
    bFault : BOOL;
    nState : INT;
    rActualLbs : REAL;
    rHoldElapsed : REAL;
END_VAR
VAR
    rCycleSeconds : REAL := 0.01;
END_VAR"""

FB_IMPLEMENTATION = """IF bReset THEN
    nState := 0;
    bPressurize := FALSE;
    bRelease := FALSE;
    bComplete := FALSE;
    bFault := FALSE;
    rActualLbs := 0.0;
    rHoldElapsed := 0.0;
ELSE
    IF bStart AND (nState = 0) THEN
        nState := 10;
        bComplete := FALSE;
        rHoldElapsed := 0.0;
        rActualLbs := 0.0;
    END_IF

    CASE nState OF
    0:
        bPressurize := FALSE;
        bRelease := FALSE;
    10:
        bPressurize := TRUE;
        bRelease := FALSE;
        rActualLbs := rActualLbs + (rPressRateLbsPerSec * rCycleSeconds);
        IF rActualLbs >= (rSetpointLbs - rToleranceLbs) THEN
            rActualLbs := rSetpointLbs;
            nState := 20;
            rHoldElapsed := 0.0;
        END_IF
    20:
        bPressurize := TRUE;
        bRelease := FALSE;
        rActualLbs := rSetpointLbs;
        rHoldElapsed := rHoldElapsed + rCycleSeconds;
        IF rHoldElapsed >= rHoldSeconds THEN
            nState := 30;
        END_IF
    30:
        bPressurize := FALSE;
        bRelease := TRUE;
        rActualLbs := rActualLbs - (rReleaseRateLbsPerSec * rCycleSeconds);
        IF rActualLbs <= 0.0 THEN
            rActualLbs := 0.0;
            nState := 40;
        END_IF
    40:
        bPressurize := FALSE;
        bRelease := FALSE;
        bComplete := TRUE;
    ELSE
        nState := 0;
    END_CASE
END_IF"""

MAIN_DECLARATION = """PROGRAM MAIN
VAR
    bStart : BOOL;
    bReset : BOOL;
    bPressurize : BOOL;
    bRelease : BOOL;
    bComplete : BOOL;
    nState : INT;
    rActualLbs : REAL;
    rHoldElapsed : REAL;
    rSetpointLbs : REAL := {setpoint};
    rAxisCommand : REAL;
    rAxisActual : REAL;
    rAxisVelocity : REAL;
    rAxisAccel : REAL;
    fbLoadControl : FB_LoadControl;
END_VAR"""

MAIN_TEMPLATE = """fbLoadControl(
    bStart := bStart,
    bReset := bReset,
    rSetpointLbs := rSetpointLbs,
    rHoldSeconds := {hold}
);

bPressurize := fbLoadControl.bPressurize;
bRelease := fbLoadControl.bRelease;
bComplete := fbLoadControl.bComplete;
nState := fbLoadControl.nState;
rActualLbs := fbLoadControl.rActualLbs;
rHoldElapsed := fbLoadControl.rHoldElapsed;

// Simulated axis response for Scope evidence. This is a PLC software model,
// not an MC3 axis and not a physical position.
IF bReset THEN
    rAxisCommand := 0.0;
    rAxisActual := 0.0;
    rAxisVelocity := 0.0;
    rAxisAccel := 0.0;
ELSE
    IF rSetpointLbs > 0.0 THEN
        rAxisCommand := (fbLoadControl.rActualLbs / rSetpointLbs) * 100.0;
    ELSE
        rAxisCommand := 0.0;
    END_IF
    rAxisAccel := (100.0 * (rAxisCommand - rAxisActual)) - (12.0 * rAxisVelocity);
    rAxisVelocity := rAxisVelocity + (rAxisAccel * 0.01);
    rAxisActual := rAxisActual + (rAxisVelocity * 0.01);
END_IF

GVL_Scope.fLoadCommandForce := rSetpointLbs;
GVL_Scope.fLoadActualForce := fbLoadControl.rActualLbs;
GVL_Scope.fSimAxisCommandPos := rAxisCommand;
GVL_Scope.fSimAxisActualPos := rAxisActual;
GVL_Scope.nLoadState := fbLoadControl.nState;
GVL_Scope.bLoadComplete := fbLoadControl.bComplete;
GVL_Scope.bLoadFault := fbLoadControl.bFault;"""

GVL_DECLARATION = """{attribute 'qualified_only'}
VAR_GLOBAL
    fLoadCommandForce : REAL;
    fLoadActualForce : REAL;
    fSimAxisCommandPos : REAL;
    fSimAxisActualPos : REAL;
    nLoadState : INT;
    bLoadComplete : BOOL;
    bLoadFault : BOOL;
END_VAR"""


def loadcontrol_sources(setpoint_lbs: float = 1000.0, hold_seconds: float = 1.0) -> tuple[str, str, str, str]:
    if setpoint_lbs <= 0:
        raise ValueError("setpoint_lbs must be positive")
    if hold_seconds <= 0:
        raise ValueError("hold_seconds must be positive")
    main_decl = MAIN_DECLARATION.format(setpoint=f"{setpoint_lbs:.3f}")
    main = MAIN_TEMPLATE.format(setpoint=f"{setpoint_lbs:.3f}", hold=f"{hold_seconds:.3f}")
    return FB_DECLARATION, FB_IMPLEMENTATION, main_decl, main


def loadcontrol_scope_gvl() -> str:
    """GVL containing the signals captured by the Scope acceptance run."""
    return GVL_DECLARATION
