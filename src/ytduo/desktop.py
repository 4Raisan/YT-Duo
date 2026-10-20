"""Native desktop integration, isolated from download policy."""
import ctypes
from ctypes import wintypes
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
import webbrowser





_launch_lock = threading.RLock()


class MonitorInfo(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                ("work", wintypes.RECT), ("flags", wintypes.DWORD)]






class WindowChrome:
    """Remove Windows caption while keeping normal taskbar/minimize semantics."""
    def __init__(self, root):
        self.root = root
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        self.user.GetAncestor.restype = ctypes.c_void_p
        self.user.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.user.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
        self.user.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                         ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        self.user.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.user.ReleaseCapture.argtypes = []
        self.user.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                         ctypes.c_size_t, ctypes.c_ssize_t]
        self.user.PostMessageW.restype = ctypes.c_int
        self.user.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
        self.user.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        self.user.MonitorFromWindow.restype = ctypes.c_void_p
        self.user.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MonitorInfo)]
        self.user.PhysicalToLogicalPointForPerMonitorDPI.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.POINT)]
        self.user.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
        self.gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        self.gdi.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
        self.gdi.CreateRoundRectRgn.restype = ctypes.c_void_p
        self.gdi.DeleteObject.argtypes = [ctypes.c_void_p]
        self._rounded_size = None
        self.hwnd = None
        root.bind("<Map>", self.apply, add="+")
        root.bind("<Configure>", self.round_corners, add="+")

    def apply(self, event=None):
        if event is not None and event.widget is not self.root:
            return
        self.hwnd = self.user.GetAncestor(self.root.winfo_id(), 2)
        style = self.user.GetWindowLongW(self.hwnd, -16)
        # Caption, sizing frame and maximize box only. Keep system menu and
        # minimize box so taskbar restore works without override-redirect.
        stripped = style & ~(0x00C00000 | 0x00040000 | 0x00010000)
        if stripped == style:
            self.round_corners()
            return
        style = stripped
        self.user.SetWindowLongW(self.hwnd, -16, style)
        self.user.SetWindowPos(self.hwnd, None, 0, 0, 0, 0, 0x0037)
        self.round_corners()

    def round_corners(self, event=None):
        if not self.hwnd or (event is not None and event.widget is not self.root):
            return
        rect = wintypes.RECT()
        if not self.user.GetWindowRect(self.hwnd, ctypes.byref(rect)):
            return
        size = rect.right - rect.left, rect.bottom - rect.top
        if min(size) < 30 or size == self._rounded_size:
            return
        region = self.gdi.CreateRoundRectRgn(0, 0, size[0]+1, size[1]+1, 24, 24)
        if region:
            # SetWindowRgn sends Configure; cache first to prevent recursion.
            self._rounded_size = size
            if not self.user.SetWindowRgn(self.hwnd, region, True):
                self.gdi.DeleteObject(region)
                self._rounded_size = None

    def minimize(self):
        if self.hwnd:
            self.user.ShowWindow(self.hwnd, 6)

    def keep_on_screen(self):
        """Fit a newly expanded layout without changing the user's drag loop."""
        if not self.hwnd:
            return
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        monitor = self.user.MonitorFromWindow(self.hwnd, 2)
        rect = wintypes.RECT()
        if not self.user.GetMonitorInfoW(monitor, ctypes.byref(info)) or not self.user.GetWindowRect(self.hwnd, ctypes.byref(rect)):
            return
        start = wintypes.POINT(info.work.left, info.work.top)
        end = wintypes.POINT(info.work.right, info.work.bottom)
        # Monitor work bounds are physical; GetWindowRect is virtualized for
        # DPI-unaware Tk windows. Convert using this window's awareness.
        if not self.user.PhysicalToLogicalPointForPerMonitorDPI(self.hwnd, ctypes.byref(start)) or not self.user.PhysicalToLogicalPointForPerMonitorDPI(self.hwnd, ctypes.byref(end)):
            return
        x = max(start.x, min(rect.left, end.x - (rect.right-rect.left)))
        y = max(start.y, min(rect.top, end.y - (rect.bottom-rect.top)))
        if (x, y) != (rect.left, rect.top):
            self.user.SetWindowPos(self.hwnd, None, x, y, 0, 0, 0x0015)

    def bind_drag(self, widget):
        def start(event):
            if not self.hwnd:
                return
            # Let Windows own the entire move operation. Rewriting Tk's
            # geometry for each motion fights its cached frame dimensions,
            # causing redraw lag, position drift and resize events on drag.
            # Release Tk's implicit mouse capture before caption hit-testing.
            position = (event.x_root & 0xffff) | ((event.y_root & 0xffff) << 16)
            self.user.ReleaseCapture()
            # Queue this after the Tk callback returns; synchronously entering
            # a nested native event loop can re-enter Tcl from a ctypes call.
            self.user.PostMessageW(self.hwnd, 0x0112, 0xF012, position)
            return "break"
        widget.bind("<ButtonPress-1>", start, add="+")




