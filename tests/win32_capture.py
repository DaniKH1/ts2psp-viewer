"""Capture a window directly with the Win32 PrintWindow API.

ImageGrab grabs the screen rectangle, which repeatedly caught the desktop or
the window behind instead of the target. PrintWindow asks the window to render
itself into a DC, so it works for an overrideredirect top level too.
"""

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32")
gdi32 = ctypes.WinDLL("gdi32")

user32.GetParent.restype = ctypes.c_void_p
user32.GetParent.argtypes = (ctypes.c_void_p,)
user32.GetWindowDC.restype = ctypes.c_void_p
user32.GetWindowDC.argtypes = (ctypes.c_void_p,)
user32.ReleaseDC.restype = ctypes.c_int
user32.ReleaseDC.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
user32.PrintWindow.restype = ctypes.c_int
user32.PrintWindow.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint)

PW_RENDERFULLCONTENT = 0x00000002


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def hwnd_of(widget):
    child = ctypes.c_void_p(widget.winfo_id())
    parent = user32.GetParent(child)
    return parent if parent else child


def grab(widget):
    """Return a PIL Image of the widget, or None if the capture failed."""
    from PIL import Image

    hwnd = hwnd_of(widget)
    width, height = widget.winfo_width(), widget.winfo_height()
    if width < 2 or height < 2:
        return None

    window_dc = user32.GetWindowDC(hwnd)
    if not window_dc:
        return None
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    gdi32.SelectObject(memory_dc, bitmap)
    try:
        ok = user32.PrintWindow(hwnd, memory_dc, PW_RENDERFULLCONTENT)
        if not ok:
            user32.PrintWindow(hwnd, memory_dc, 0)

        class BITMAP(ctypes.Structure):
            _fields_ = [
                ("bmType", wintypes.DWORD),
                ("bmWidth", ctypes.c_long),
                ("bmHeight", ctypes.c_long),
                ("bmWidthBytes", wintypes.WORD),
                ("bmPlanes", wintypes.WORD),
                ("bmBitsPixel", wintypes.WORD),
                ("bmBits", ctypes.POINTER(ctypes.c_ubyte)),
            ]

        info = BITMAP()
        info.bmType = 0
        info.bmWidth = width
        info.bmHeight = -height  # negative: top-down
        info.bmWidthBytes = ((width * 32 + 31) // 32) * 4
        info.bmPlanes = 1
        info.bmBitsPixel = 32
        buffer = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(info), 0)
        return Image.frombuffer(
            "RGBA", (width, height), buffer, "raw", "BGRA", 0, 1
        ).convert("RGBA")
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)
