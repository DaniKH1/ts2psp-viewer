"""Open the built EXE, confirm its own window appears, then close it."""

import argparse
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import time


class ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("usage", wintypes.DWORD),
        ("pid", wintypes.DWORD),
        ("heap", ctypes.c_size_t),
        ("module", wintypes.DWORD),
        ("threads", wintypes.DWORD),
        ("parent", wintypes.DWORD),
        ("priority", wintypes.LONG),
        ("flags", wintypes.DWORD),
        ("name", wintypes.WCHAR * 260),
    ]


def descendants(parent):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = kernel.Process32FirstW.argtypes
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    links = {}
    entry = ProcessEntry()
    entry.size = ctypes.sizeof(entry)
    valid = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
    while valid:
        links[entry.pid] = entry.parent
        valid = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    kernel.CloseHandle(snapshot)
    result = {parent}
    while True:
        found = {pid for pid, owner in links.items() if owner in result}
        if found <= result:
            return result
        result |= found


def smoke(exe, data=None):
    environment = dict(os.environ)
    if data:
        environment["TS2_PSP_DATA_DIR"] = str(data)
    process = subprocess.Popen([str(exe)], env=environment)
    user = ctypes.WinDLL("user32")
    found = []
    owned = {process.pid}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(window, unused):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(window, ctypes.byref(pid))
        if pid.value in owned and user.IsWindowVisible(window):
            text = ctypes.create_unicode_buffer(256)
            user.GetWindowTextW(window, text, len(text))
            if text.value.startswith("The Sims 2 PSP"):
                found.append(window)
        return True

    try:
        for _ in range(60):
            owned = descendants(process.pid)
            user.EnumWindows(visit, 0)
            if found or process.poll() is not None:
                break
            time.sleep(0.5)
        if not found:
            raise RuntimeError(f"EXE window did not appear; exit={process.poll()}")
        user.PostMessageW(found[0], 0x0010, 0, 0)
        process.wait(timeout=20)
        if process.returncode:
            raise RuntimeError(f"EXE exited with {process.returncode}")
        print("EXE window opened and closed successfully")
    finally:
        if process.poll() is None:
            process.terminate()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exe", type=Path)
    parser.add_argument("--data", type=Path)
    args = parser.parse_args()
    smoke(args.exe.resolve(), args.data)
