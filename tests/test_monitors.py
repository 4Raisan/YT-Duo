"""Window placement and Canvas bounds regressions."""
import ctypes
from ctypes import wintypes
import re
import sys
from unittest import mock
from tests.test_gui import GuiTestCase
from ytduo import desktop

class MonitorLayout(GuiTestCase):
    def test_windows_layout_uses_captured_monitor_without_primary_screen_coordinates(self):
        original = self.app.chrome
        bounds = (-1366, -120, 0, 648)
        chrome = mock.Mock()
        chrome.work_area.return_value = bounds
        chrome.layout_height.return_value = bounds[3] - bounds[1]
        self.app.chrome = chrome
        try:
            with mock.patch.object(self.tk, "geometry", wraps=self.tk.geometry) as geometry:
                self.app.toggle_settings(True)
                self.app._on_update_result({"status": "available", "latest": "99.0.0"})
                self.app.toggle_log(True)
            self.assertTrue(geometry.called)
            for call in geometry.call_args_list:
                self.assertRegex(call.args[0], r"^\d+x\d+$")
            chrome.keep_on_screen.assert_called_with(bounds)
        finally:
            self.app.chrome = original

    def test_active_button_outlines_stay_inside_canvas(self):
        for scale in (90, 100, 110, 125):
            self.app.apply_font_scale(scale)
            self.app.toggle_settings(True)
            self.app._on_update_result({"status": "available", "latest": "99.0.0"})
            for button in (self.app.settings_button, self.app.terminal_button, self.app.update_button):
                for pressed in (False, True):
                    button._pressed = pressed
                    button.repaint()
                    for item in button.find_all():
                        if button.type(item) != "polygon": continue
                        box = button.bbox(item)
                        self.assertGreaterEqual(box[0], 0)
                        self.assertGreaterEqual(box[1], 0)
                        self.assertLessEqual(box[2], button._width)
                        self.assertLessEqual(box[3], button.height)
                button._pressed = False

    def test_update_button_only_appears_for_available_release(self):
        self.assertEqual(self.app.update_button.winfo_manager(), "")
        for status in ("current", "unavailable", "unpublished"):
            self.app._on_update_result({"status": status})
            self.assertEqual(self.app.update_button.winfo_manager(), "")
        self.app._on_update_result({"status": "available", "latest": "99.0.0"})
        self.assertEqual(self.app.update_button.winfo_manager(), "pack")
        self.assertEqual(self.app.update_button.kind, "update")
        self.app._on_update_result({"status": "current"})
        self.assertEqual(self.app.update_button.winfo_manager(), "")

    def test_actual_mixed_monitors_keep_header_and_controls_on_screen(self):
        if sys.platform != "win32": self.skipTest("Windows monitor placement")
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(desktop.MonitorInfo)]
        user.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
        user.GetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        gdi.CreateRectRgn.argtypes = [ctypes.c_int]*4
        gdi.CreateRectRgn.restype = ctypes.c_void_p
        gdi.GetRgnBox.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
        gdi.DeleteObject.argtypes = [ctypes.c_void_p]
        user.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        callback = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wintypes.RECT), ctypes.c_ssize_t)
        monitors = []
        def collect(handle, dc, rect, data):
            info = desktop.MonitorInfo(); info.size = ctypes.sizeof(info)
            user.GetMonitorInfoW(handle, ctypes.byref(info))
            monitors.append(tuple(getattr(info.work, k) for k in ("left", "top", "right", "bottom")))
            return 1
        context = user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        try: user.EnumDisplayMonitors(None, None, callback(collect), 0)
        finally: user.SetThreadDpiAwarenessContext(context)
        if len(monitors) < 2: self.skipTest("Requires two physical monitors")
        self.tk.attributes("-alpha", 0)
        self.tk.deiconify(); self.tk.update(); self.app.chrome.apply()
        hwnd = self.app.chrome.hwnd
        for scale in (100, 125):
            for bounds in monitors:
                context = user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
                try: user.SetWindowPos(hwnd, None, bounds[0]+25, bounds[3]-90, 0, 0, 0x0015)
                finally: user.SetThreadDpiAwarenessContext(context)
                self.tk.update()
                self.app.apply_font_scale(scale)
                for action in (lambda: self.app.toggle_settings(True),
                               lambda: self.app._on_update_result({"status": "available", "latest": "99.0.0"}),
                               lambda: self.app.toggle_log(True),
                               lambda: self.app.toggle_settings(True)):
                    action(); self.tk.update()
                    context = user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
                    try:
                        rect = wintypes.RECT(); user.GetWindowRect(hwnd, ctypes.byref(rect))
                        region = gdi.CreateRectRgn(0,0,0,0)
                        region_box = wintypes.RECT()
                        try:
                            self.assertNotEqual(user.GetWindowRgn(hwnd,region),0)
                            gdi.GetRgnBox(region,ctypes.byref(region_box))
                        finally: gdi.DeleteObject(region)
                    finally: user.SetThreadDpiAwarenessContext(context)
                    with self.subTest(scale=scale, monitor=bounds):
                        self.assertGreaterEqual(region_box.right, rect.right-rect.left)
                        self.assertGreaterEqual(region_box.bottom, rect.bottom-rect.top)
                        self.assertLessEqual(region_box.right, rect.right-rect.left+1)
                        self.assertLessEqual(region_box.bottom, rect.bottom-rect.top+1)
                        self.assertGreaterEqual(rect.left, bounds[0])
                        self.assertGreaterEqual(rect.top, bounds[1])
                        self.assertLessEqual(rect.right, bounds[2])
                        self.assertLessEqual(rect.bottom, bounds[3])
