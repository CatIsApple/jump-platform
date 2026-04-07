"""Runtime anti-reverse-engineering guard.

Nuitka compiled binary already has no Python bytecode.
This module adds runtime debugger/tool detection as an extra layer.
"""
from __future__ import annotations

import os
import sys


def _check_debugger() -> bool:
    """Windows API debugger detection."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        # IsDebuggerPresent
        if k32.IsDebuggerPresent():
            return True

        # CheckRemoteDebuggerPresent
        flag = wintypes.BOOL(False)
        k32.CheckRemoteDebuggerPresent(k32.GetCurrentProcess(), ctypes.byref(flag))
        if flag.value:
            return True

        # NtQueryInformationProcess — DebugPort (ProcessInfoClass=7)
        try:
            ntdll = ctypes.windll.ntdll  # type: ignore[attr-defined]
            dbg_port = ctypes.c_void_p(0)
            st = ntdll.NtQueryInformationProcess(
                k32.GetCurrentProcess(), 7,
                ctypes.byref(dbg_port), ctypes.sizeof(dbg_port), None,
            )
            if st == 0 and dbg_port.value:
                return True
        except Exception:
            pass

        # NtQueryInformationProcess — DebugFlags (ProcessInfoClass=31)
        try:
            dbg_flags = ctypes.c_ulong(1)
            st = ntdll.NtQueryInformationProcess(
                k32.GetCurrentProcess(), 31,
                ctypes.byref(dbg_flags), ctypes.sizeof(dbg_flags), None,
            )
            if st == 0 and dbg_flags.value == 0:
                return True
        except Exception:
            pass

    except Exception:
        pass
    return False


def _check_hostile_processes() -> bool:
    """Detect known reverse-engineering tools by process name."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        import ctypes.wintypes as wt

        TH32CS_SNAPPROCESS = 0x00000002

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wt.DWORD),
                ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wt.DWORD),
                ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_char * 260),
            ]

        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == -1:
            return False

        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(pe)

        _BLACKLIST = frozenset([
            b"ollydbg", b"x64dbg", b"x32dbg", b"windbg",
            b"ida", b"ida64", b"idag", b"idag64", b"idaq", b"idaq64",
            b"ghidra", b"ghidrarun",
            b"dnspy", b"dotpeek", b"ilspy", b"de4dot",
            b"procmon", b"procexp", b"procexp64",
            b"wireshark", b"fiddler", b"charles",
            b"httpanalyzer", b"httpdebugger",
            b"cheatengine", b"ce",
            b"hiew", b"lordpe", b"petools",
            b"importrec", b"scylla",
            b"pestudio", b"detect_it_easy", b"die",
            b"pyinstxtractor", b"uncompyle6", b"decompyle3", b"pycdc",
        ])

        found = False
        if k32.Process32First(snap, ctypes.byref(pe)):
            while True:
                name = pe.szExeFile.split(b"\\")[-1].split(b".")[0].lower().strip()
                if name in _BLACKLIST:
                    found = True
                    break
                if not k32.Process32Next(snap, ctypes.byref(pe)):
                    break

        k32.CloseHandle(snap)
        return found
    except Exception:
        return False


def run_guard() -> None:
    """Execute all anti-RE checks. Silently terminates if detected."""
    if sys.platform != "win32":
        return
    if _check_debugger() or _check_hostile_processes():
        os._exit(0)
