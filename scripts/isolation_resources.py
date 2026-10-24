"""Best-effort process observations using OS interfaces, never cloud telemetry."""

import os
from pathlib import Path


def observe(pid):
    if os.name != "nt":
        stat = Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()
        return {
            "cpu_seconds": (int(stat[11]) + int(stat[12])) / os.sysconf("SC_CLK_TCK"),
            "rss_bytes": int(stat[21]) * os.sysconf("SC_PAGE_SIZE"),
        }
    import ctypes
    from ctypes import wintypes

    class Memory(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t)
            for name in (
                "peak",
                "rss",
                "peak_paged",
                "paged",
                "peak_nonpaged",
                "nonpaged",
                "pagefile",
                "peak_pagefile",
            )
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x410, False, pid)
    if not handle:
        raise OSError("process observation denied")
    try:
        values = [ctypes.c_ulonglong() for _ in range(4)]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in values)):
            raise OSError("process time unavailable")
        memory = Memory()
        memory.cb = ctypes.sizeof(memory)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Memory), wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb):
            raise OSError("process memory unavailable")
        return {"cpu_seconds": (values[2].value + values[3].value) / 1e7, "rss_bytes": memory.rss}
    finally:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)
