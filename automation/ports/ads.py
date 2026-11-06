from __future__ import annotations

from typing import Protocol


class AdsPort(Protocol):
    """Boundary for ADS reads used by the simulation test rig."""

    def ensure_running(self) -> None:
        """Ensure the local simulation PLC is in RUN mode."""

    def read_symbol(self, symbol: str, value_type: str = "BOOL") -> object:
        """Read one PLC symbol from the simulation runtime (no state writes)."""

    def write_symbol(self, symbol: str, value: object, value_type: str = "BOOL") -> None:
        """Write one PLC symbol on the local simulation runtime."""

    def close(self) -> None:
        """Release the ADS connection and any notification handles."""
