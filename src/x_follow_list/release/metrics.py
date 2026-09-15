from __future__ import annotations

import ctypes
import os
from pathlib import Path


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def process_rss_bytes() -> int:
    """Return current resident memory without an optional process dependency."""
    if os.name == "nt":
        return int(_windows_memory_counters().WorkingSetSize)

    statm = Path("/proc/self/statm")
    if statm.is_file():
        resident_pages = int(statm.read_text(encoding="ascii").split()[1])
        page_size = int(os.sysconf("SC_PAGE_SIZE"))  # type: ignore[attr-defined]
        return resident_pages * page_size
    raise OSError("resident memory is unavailable on this platform")


def process_peak_rss_bytes() -> int:
    """Return peak resident memory for the current process."""
    if os.name == "nt":
        return int(_windows_memory_counters().PeakWorkingSetSize)
    status = Path("/proc/self/status")
    if status.is_file():
        for line in status.read_text(encoding="ascii").splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    raise OSError("peak resident memory is unavailable on this platform")


def _windows_memory_counters() -> _ProcessMemoryCounters:
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    get_current_process = ctypes.windll.kernel32.GetCurrentProcess
    get_current_process.restype = ctypes.c_void_p
    get_process_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
    get_process_memory_info.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_ProcessMemoryCounters),
        ctypes.c_ulong,
    ]
    get_process_memory_info.restype = ctypes.c_int
    succeeded = get_process_memory_info(
        get_current_process(), ctypes.byref(counters), counters.cb
    )
    if not succeeded:
        raise OSError("GetProcessMemoryInfo failed")
    return counters
