from __future__ import annotations

import time


class PyadsAdapter:
    """ADS adapter for a local simulation runtime.

    `read_symbol` is read-only. A separate `ensure_running` operation performs
    the single allowed control write, and the runner calls it only after the
    simulation safety policy has passed.
    """

    def __init__(self, netid: str = "127.0.0.1.1.1", port: int = 851, retries: int = 60) -> None:
        if netid not in {"127.0.0.1.1.1", "localhost"}:
            raise ValueError("PyadsAdapter is restricted to the local simulation NetId")
        if port != 851:
            raise ValueError("PyadsAdapter is restricted to the local PLC port 851")
        self.netid = netid
        self.port = port
        self.retries = retries
        self._connection = None

    def ensure_running(self) -> None:
        import pyads
        from pyads import pyads_ex

        connection = self._connect()
        last_error = None
        for _ in range(self.retries):
            try:
                state = connection.read_state()
                if state[0] == pyads.ADSSTATE_RUN:
                    return
                pyads_ex.adsSyncWriteControlReqEx(
                    connection._port,
                    connection._adr,
                    pyads.ADSSTATE_RUN,
                    0,
                    0,
                    pyads.PLCTYPE_BOOL,
                )
                for _ in range(20):
                    if connection.read_state()[0] == pyads.ADSSTATE_RUN:
                        return
                    time.sleep(0.1)
                raise RuntimeError("PLC did not confirm RUN state")
            except Exception as exc:
                last_error = exc
                time.sleep(0.5)
        raise RuntimeError(f"could not start local simulation PLC: {last_error}") from last_error

    def system_state(self) -> tuple[int, int]:
        """Read the local TwinCAT system service state on port 10000."""
        import pyads

        connection = pyads.Connection(self.netid, 10000)
        connection.open()
        try:
            return connection.read_state()
        finally:
            connection.close()

    def wait_for_service(self, timeout_s: float = 120.0) -> tuple[int, int]:
        """Wait until the system service answers in CONFIG or RUN."""
        import pyads

        deadline = time.monotonic() + max(1.0, timeout_s)
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                state = self.system_state()
                if state[0] in {pyads.ADSSTATE_CONFIG, pyads.ADSSTATE_RUN}:
                    return state
            except Exception as exc:
                last_error = exc
            time.sleep(1.0)
        raise RuntimeError(f"TwinCAT system service not ready: {last_error}")

    def symbols_ready(self) -> bool:
        """Return true only when the PLC publishes a non-empty symbol table."""
        import pyads

        connection = None
        try:
            connection = pyads.Connection(self.netid, self.port)
            connection.open()
            return bool(connection.get_all_symbols())
        except Exception:
            return False
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    def reconfigure(self, timeout_s: float = 45.0, settle_s: float = 2.0) -> dict:
        """Return the TwinCAT system to CONFIG before a new activation.

        The UM runtime can stay in RUN with an empty PLC symbol table after a
        failed download.  An explicit RECONFIG clears that state and makes the
        next activation deterministic.  ADS port 10000 is briefly disabled
        during the transition, so state reads are retried until the timeout.
        """
        import pyads

        connection = pyads.Connection(self.netid, 10000)
        connection.open()
        deadline = time.monotonic() + max(1.0, timeout_s)
        before = None
        try:
            while time.monotonic() < deadline:
                try:
                    before = connection.read_state()
                    break
                except Exception:
                    time.sleep(0.25)
            if before is None:
                raise RuntimeError("TwinCAT system service did not answer on port 10000")

            if before[0] != pyads.ADSSTATE_CONFIG:
                while time.monotonic() < deadline:
                    try:
                        connection.write_control(
                            pyads.ADSSTATE_RECONFIG,
                            0,
                            0,
                            pyads.PLCTYPE_BOOL,
                        )
                        break
                    except Exception:
                        time.sleep(0.25)

            observed = before
            while time.monotonic() < deadline:
                try:
                    observed = connection.read_state()
                except Exception:
                    time.sleep(0.25)
                    continue
                if observed[0] == pyads.ADSSTATE_CONFIG:
                    if settle_s > 0:
                        time.sleep(settle_s)
                    return {"before": before, "after": observed, "settled_s": settle_s}
                time.sleep(0.25)
            raise RuntimeError(f"TwinCAT did not enter CONFIG state: {observed}")
        finally:
            connection.close()

    def read_symbol(self, symbol: str, value_type: str = "BOOL") -> object:
        import pyads

        plc_type = getattr(pyads, f"PLCTYPE_{value_type.upper()}", None)
        if plc_type is None:
            raise ValueError(f"unsupported ADS value type: {value_type}")

        connection = self._connect()
        last_error = None
        for _ in range(self.retries):
            try:
                return connection.read_by_name(symbol, plc_type)
            except Exception as exc:
                last_error = exc
                time.sleep(0.5)
        raise RuntimeError(f"ADS read failed for {symbol}: {last_error}") from last_error

    def wait_symbols(self, timeout_s: float = 30.0) -> list[dict]:
        """Wait until the runtime publishes a non-empty symbol table."""
        deadline = time.monotonic() + max(1.0, timeout_s)
        last_error = None
        while time.monotonic() < deadline:
            try:
                symbols = self.discover_symbols()
                if symbols:
                    return symbols
            except Exception as exc:
                last_error = exc
            time.sleep(0.5)
        raise RuntimeError(f"ADS symbol table not ready: {last_error}")

    def discover_symbols(self) -> list[dict]:
        connection = self._connect()
        result = []
        for symbol in connection.get_all_symbols():
            result.append(
                {
                    "name": symbol.name,
                    "type": symbol.symbol_type,
                    "index_group": symbol.index_group,
                    "index_offset": symbol.index_offset,
                    "array_size": symbol.array_size,
                    "comment": symbol.comment,
                }
            )
        return result

    def state(self) -> dict:
        import pyads

        connection = self._connect()
        ads_state, device_state = connection.read_state()
        names = {
            pyads.ADSSTATE_INVALID: "INVALID",
            pyads.ADSSTATE_IDLE: "IDLE",
            pyads.ADSSTATE_RESET: "RESET",
            pyads.ADSSTATE_INIT: "INIT",
            pyads.ADSSTATE_START: "START",
            pyads.ADSSTATE_RUN: "RUN",
            pyads.ADSSTATE_STOP: "STOP",
            pyads.ADSSTATE_SAVECFG: "SAVECFG",
            pyads.ADSSTATE_LOADCFG: "LOADCFG",
            pyads.ADSSTATE_POWERFAILURE: "POWERFAILURE",
            pyads.ADSSTATE_POWERGOOD: "POWERGOOD",
            pyads.ADSSTATE_ERROR: "ERROR",
            pyads.ADSSTATE_SHUTDOWN: "SHUTDOWN",
            pyads.ADSSTATE_SUSPEND: "SUSPEND",
            pyads.ADSSTATE_RESUME: "RESUME",
            pyads.ADSSTATE_RECONFIG: "RECONFIG",
        }
        return {
            "connected": True,
            "netid": self.netid,
            "port": self.port,
            "ads_state": ads_state,
            "ads_state_name": names.get(ads_state, str(ads_state)),
            "device_state": device_state,
        }

    def write_symbol(self, symbol: str, value: object, value_type: str = "BOOL") -> None:
        import pyads

        plc_type = getattr(pyads, f"PLCTYPE_{value_type.upper()}", None)
        if plc_type is None:
            raise ValueError(f"unsupported ADS value type: {value_type}")
        connection = self._connect()
        last_error = None
        for _ in range(self.retries):
            try:
                connection.write_by_name(symbol, value, plc_type)
                return
            except Exception as exc:
                last_error = exc
                time.sleep(0.5)
        raise RuntimeError(f"ADS write failed for {symbol}: {last_error}") from last_error

    def close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None

    def _connect(self):
        import pyads

        if self._connection is None:
            connection = pyads.Connection(self.netid, self.port)
            try:
                connection.open()
                pyads.set_timeout(5000)
            except Exception:
                try:
                    connection.close()
                finally:
                    raise
            self._connection = connection
        return self._connection
