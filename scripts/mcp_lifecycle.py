"""Keep MCP server processes tied to the process that launched them.

Two mechanisms are installed on Windows:

* a job object with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``; every child of the
  server (notably ``TcXaeShell``) is terminated if the server dies, even when
  the parent used TerminateProcess instead of closing stdin;
* a small watchdog that exits the server when its parent process disappears.

Together they mean an MCP server cannot outlive DSH/OpenCode, and an XAE shell
started by ``tc-auto`` cannot outlive the MCP server.  Non-Windows hosts only
get the parent watchdog.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time

_JOB_HANDLE = None
_INSTALLED = False


if os.name == "nt":
    from ctypes import wintypes

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _KERNEL32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _KERNEL32.CreateJobObjectW.restype = wintypes.HANDLE
    _KERNEL32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _KERNEL32.SetInformationJobObject.restype = wintypes.BOOL
    _KERNEL32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _KERNEL32.AssignProcessToJobObject.restype = wintypes.BOOL
    _KERNEL32.GetCurrentProcess.restype = wintypes.HANDLE
    _KERNEL32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _KERNEL32.OpenProcess.restype = wintypes.HANDLE
    _KERNEL32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _KERNEL32.WaitForSingleObject.restype = wintypes.DWORD
    _KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
    _KERNEL32.CloseHandle.restype = wintypes.BOOL

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _SYNCHRONIZE = 0x00100000
    _WAIT_OBJECT_0 = 0
    _WAIT_TIMEOUT = 0x00000102


def enable_kill_on_close() -> bool:
    """Put this process in a kill-on-close job; children inherit it."""
    global _JOB_HANDLE
    if os.name != "nt":
        return False
    if _JOB_HANDLE is not None:
        return True
    handle = _KERNEL32.CreateJobObjectW(None, None)
    if not handle:
        return False
    info = _EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    configured = _KERNEL32.SetInformationJobObject(
        handle,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    assigned = configured and _KERNEL32.AssignProcessToJobObject(
        handle, _KERNEL32.GetCurrentProcess()
    )
    if not assigned:
        _KERNEL32.CloseHandle(handle)
        return False
    _JOB_HANDLE = handle
    return True


def watch_parent(interval: float = 1.0) -> bool:
    """Exit this process when its parent exits."""
    parent = os.getppid()
    if parent <= 0:
        return False

    def run() -> None:
        if os.name == "nt":
            handle = _KERNEL32.OpenProcess(_SYNCHRONIZE, False, parent)
            if not handle:
                return
            try:
                while _KERNEL32.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT:
                    time.sleep(interval)
            finally:
                _KERNEL32.CloseHandle(handle)
        else:
            while os.getppid() == parent:
                time.sleep(interval)
        os._exit(0)

    threading.Thread(target=run, name="mcp-parent-watchdog", daemon=True).start()
    return True


def install() -> tuple[bool, bool]:
    """Install the job and watchdog once; safe to call from any server."""
    global _INSTALLED
    if _INSTALLED:
        return (_JOB_HANDLE is not None, True)
    if os.environ.get("TC_MCP_LIFECYCLE") == "1":
        _INSTALLED = True
        return (os.name == "nt", True)
    job = enable_kill_on_close()
    watched = watch_parent()
    if job:
        os.environ["TC_MCP_LIFECYCLE"] = "1"
    _INSTALLED = True
    if os.name == "nt" and not job:
        sys.stderr.write("TC-MCP lifecycle: kill-on-close job unavailable\n")
        sys.stderr.flush()
    return (job, watched)
