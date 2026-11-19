"""Orphan watchdog.

MCP clients spawn this server via ``uv run``. Reproduced live on Windows:
when the ``uv`` wrapper is killed while the client's stdin pipe stays
open, the python process never sees stdin EOF and keeps holding the
bridge port forever — every subsequent instance then fails to bind.

The entry-point launcher may sit between uv and this process, so watching
only the direct parent is not enough: snapshot the whole ancestor chain
at startup and exit when ANY of those ancestors disappears.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import sys

log = logging.getLogger(__name__)

POLL_S = float(os.environ.get("CISCO_PT_MCP_WATCHDOG_INTERVAL", "0"))


def _windows_ancestors() -> list[int]:
    """Ancestor pids via a Toolhelp32 process snapshot."""
    TH32CS_SNAPPROCESS = 0x2
    INVALID_HANDLE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", ctypes.c_ulong),
            ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE or snap is None:
        return []
    try:
        parents: dict[int, int] = {}
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)

    out: list[int] = []
    pid = parents.get(os.getpid())
    while pid and pid not in out:
        out.append(pid)
        pid = parents.get(pid)
    return out


def _posix_ancestors() -> list[int]:
    """Ancestor pids by walking /proc ppids."""
    out: list[int] = []
    pid = os.getpid()
    for _ in range(32):
        try:
            with open(f"/proc/{pid}/stat", "rb") as fh:
                # ppid is the 4th field; comm may contain spaces/parens so
                # split after the last ')'.
                stat = fh.read().decode()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return out
        if ppid <= 1 or ppid in out:
            return out
        out.append(ppid)
        pid = ppid
    return out


def ancestor_pids() -> list[int]:
    try:
        if sys.platform == "win32":
            return _windows_ancestors()
        return _posix_ancestors()
    except Exception:  # noqa: BLE001
        log.debug("could not enumerate ancestors", exc_info=True)
        return []


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        ctypes.set_last_error(0)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            # Access denied still means the process exists.
            return ctypes.get_last_error() == 5
        # A killed process can linger in the table while handles to it are
        # still open elsewhere — only STILL_ACTIVE counts as alive.
        code = ctypes.c_ulong(0)
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        if not ok:
            return True  # can't tell — assume alive
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


async def orphan_watchdog() -> None:
    """Exit the process once any startup ancestor is gone.

    Uses ``os._exit`` because the stdio read loop may be blocked
    indefinitely; the OS releases the listening socket on exit.
    Set ``CISCO_PT_MCP_WATCHDOG_INTERVAL=0`` to disable.
    """
    if POLL_S <= 0:
        return
    ancestors = ancestor_pids()
    if not ancestors:
        return
    log.debug("watchdog tracking ancestors: %s", ancestors)
    while True:
        await asyncio.sleep(POLL_S)
        dead = [p for p in ancestors if not _pid_alive(p)]
        if dead:
            log.error(
                "ancestor process(es) %s exited — cisco-pt-mcp is orphaned; "
                "shutting down to release the bridge port",
                dead,
            )
            os._exit(0)
