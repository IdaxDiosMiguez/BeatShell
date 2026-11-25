"""Cuánta memoria usa BeatShell ahora mismo, y cómo devolver al sistema la que sobra."""

from __future__ import annotations

import ctypes
import gc
import os
import subprocess
import sys


def rss_mb(pid: int | None = None) -> float | None:
    """Memoria residente de un proceso en MB (None si no se puede saber)."""
    pid = pid or os.getpid()
    try:
        if sys.platform.startswith("linux"):
            with open(f"/proc/{pid}/statm") as fh:
                return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2**20
        if sys.platform == "win32":
            return _win_rss(pid)
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=2).stdout.strip()
        return int(out) / 1024 if out else None
    except Exception:  # noqa: BLE001
        return None


def _win_rss(pid: int) -> float | None:
    from ctypes import wintypes

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    h = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x0010, False, pid)  # QUERY_LIMITED | VM_READ
    if not h:
        return None
    try:
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        if ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
            return pmc.WorkingSetSize / 2**20
        return None
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def give_back() -> None:
    """Recoge la basura de Python y pide al sistema que recupere la memoria libre."""
    gc.collect()
    try:
        if sys.platform == "darwin":
            ctypes.CDLL(None).malloc_zone_pressure_relief(None, 0)
        elif sys.platform.startswith("linux"):
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        elif sys.platform == "win32":
            k32 = ctypes.windll.kernel32
            ctypes.windll.psapi.EmptyWorkingSet(k32.GetCurrentProcess())
    except Exception:  # noqa: BLE001 - es solo una ayuda
        pass
