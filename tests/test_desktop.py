"""Regression tests for frozen-process Explorer DLL contamination."""
import unittest
from unittest import mock
from ytduo import desktop


class ShellEnvironmentTests(unittest.TestCase):
    def kernel(self):
        kernel = mock.Mock()
        def directory(size, buffer):
            if buffer is not None:
                buffer.value = r"C:\YT-Duo\_internal"
            return 24
        kernel.GetDllDirectoryW.side_effect = directory
        kernel.SetDllDirectoryW.return_value = 1
        return kernel

    def test_frozen_launch_clears_and_restores_exact_dll_directory(self):
        kernel = self.kernel()
        with mock.patch.object(desktop.sys, "frozen", True, create=True), \
             mock.patch.object(desktop.sys, "platform", "win32"), \
             mock.patch.object(desktop.ctypes, "WinDLL", return_value=kernel, create=True):
            with desktop.shell_environment():
                kernel.SetDllDirectoryW.assert_called_once_with(None)
            self.assertEqual(kernel.SetDllDirectoryW.call_args_list,
                             [mock.call(None), mock.call(r"C:\YT-Duo\_internal")])

    def test_failed_launch_still_restores_dll_directory(self):
        kernel = self.kernel()
        with mock.patch.object(desktop.sys, "frozen", True, create=True), \
             mock.patch.object(desktop.sys, "platform", "win32"), \
             mock.patch.object(desktop.ctypes, "WinDLL", return_value=kernel, create=True):
            with self.assertRaisesRegex(OSError, "denied"):
                with desktop.shell_environment():
                    raise OSError("denied")
            kernel.SetDllDirectoryW.assert_called_with(r"C:\YT-Duo\_internal")

    def test_unfrozen_process_does_not_change_dll_search(self):
        with mock.patch.object(desktop.sys, "frozen", False, create=True), \
             mock.patch.object(desktop.ctypes, "WinDLL", create=True) as load:
            with desktop.shell_environment():
                pass
            load.assert_not_called()


class NativeDragTests(unittest.TestCase):
    def test_expanded_layout_stays_on_monitor_without_resizing_or_drag_loop(self):
        chrome = desktop.WindowChrome.__new__(desktop.WindowChrome)
        chrome.hwnd, chrome.user = 123, mock.Mock()
        def work_area(monitor, pointer):
            pointer._obj.work.left, pointer._obj.work.top = 0, 0
            pointer._obj.work.right, pointer._obj.work.bottom = 1920, 1040
            return 1
        def rectangle(hwnd, pointer):
            pointer._obj.left, pointer._obj.top = 80, 800
            pointer._obj.right, pointer._obj.bottom = 824, 1612
            return 1
        def logical(hwnd, pointer):
            pointer._obj.x = round(pointer._obj.x / 1.25)
            pointer._obj.y = round(pointer._obj.y / 1.25)
            return 1
        chrome.user.IsIconic.return_value = False
        chrome.user.GetMonitorInfoW.side_effect = work_area
        chrome.user.GetWindowRect.side_effect = rectangle
        chrome.user.PhysicalToLogicalPointForPerMonitorDPI.side_effect = logical
        chrome.keep_on_screen()
        chrome.user.SetWindowPos.assert_called_once_with(123, None, 80, 228, 0, 0, 0x0015)

    def test_negative_monitor_origins_are_preserved_when_fitting(self):
        chrome = desktop.WindowChrome.__new__(desktop.WindowChrome)
        chrome.hwnd, chrome.user = 123, mock.Mock()
        chrome.user.IsIconic.return_value = False
        def rectangle(hwnd, pointer):
            pointer._obj.left, pointer._obj.top = -1650, -950
            pointer._obj.right, pointer._obj.bottom = -850, -200
            return 1
        chrome.user.GetWindowRect.side_effect = rectangle
        chrome.keep_on_screen((-1600,-900,0,0))
        chrome.user.SetWindowPos.assert_called_once_with(123,None,-1600,-900,0,0,0x0015)

    def test_drag_uses_windows_move_loop_without_tk_geometry_updates(self):
        chrome = desktop.WindowChrome.__new__(desktop.WindowChrome)
        chrome.root = mock.Mock()
        chrome.user = mock.Mock()
        # Also guard against truncating a 64-bit window handle.
        chrome.hwnd = 0x123456789
        widget = mock.Mock()
        chrome.bind_drag(widget)
        widget.bind.assert_called_once()
        callback = widget.bind.call_args.args[1]
        self.assertEqual(widget.bind.call_args.args[0], "<ButtonPress-1>")
        event = mock.Mock(x_root=-300, y_root=150)
        self.assertEqual(callback(event), "break")
        self.assertEqual(chrome.user.method_calls, [
            mock.call.ReleaseCapture(),
            mock.call.PostMessageW(chrome.hwnd, 0x0112, 0xF012,
                                  (-300 & 0xffff) | (150 << 16))])
        chrome.root.geometry.assert_not_called()


class NativeFolderTests(unittest.TestCase):
    def test_helper_failure_never_changes_live_dll_state_and_browser_can_retry(self):
        with mock.patch.object(desktop.sys, "frozen", True, create=True), \
             mock.patch.object(desktop.sys, "platform", "win32"), \
             mock.patch.dict(desktop.os.environ, {"YTDUO_SHELL_HELPER": r"C:\cache\helper.exe", "_PYI_ARCHIVE_FILE": "private", "TCL_LIBRARY": "private"}), \
             mock.patch.object(desktop.os.path, "isfile", return_value=True), \
             mock.patch.object(desktop, "shell_environment") as mutate, \
             mock.patch.object(desktop.subprocess, "run", side_effect=[mock.Mock(returncode=5), mock.Mock(returncode=0)]) as run:
            with self.assertRaisesRegex(OSError, "00000005"):
                desktop.open_folder_windows(r"C:\Music")
            desktop.open_url("https://github.com/4Raisan/YT-Duo")
            self.assertNotIn("_PYI_ARCHIVE_FILE", run.call_args.kwargs["env"])
            self.assertNotIn("TCL_LIBRARY", run.call_args.kwargs["env"])
            mutate.assert_not_called()
        self.assertEqual(run.call_count, 2)

    def test_shell_navigates_to_folder_and_releases_com_resources(self):
        for result in (0, -2147024891):
            shell, ole = mock.Mock(), mock.Mock()
            ole.CoInitializeEx.return_value = 0
            def parse(path, context, pidl, flags, attributes):
                pidl._obj.value = 12345
                return 0
            shell.SHParseDisplayName.side_effect = parse
            shell.SHOpenFolderAndSelectItems.return_value = result
            def library(name, **kwargs):
                return {"shell32": shell, "ole32": ole}[name]
            with mock.patch.object(desktop.ctypes, "WinDLL", side_effect=library, create=True), \
                 mock.patch.object(desktop, "shell_environment"):
                if result < 0:
                    with self.assertRaisesRegex(OSError, "80070005"):
                        desktop.open_folder_windows("C:\\Folder with spaces")
                else:
                    desktop.open_folder_windows("C:\\Folder with spaces")
            self.assertEqual(shell.SHOpenFolderAndSelectItems.call_args.args[1], 1)
            ole.CoTaskMemFree.assert_called_once()
            self.assertEqual(ole.CoTaskMemFree.call_args.args[0].value, 12345)
            ole.CoUninitialize.assert_called_once()

    def test_packaged_folder_open_uses_isolated_helper_and_argument_list(self):
        with mock.patch.object(desktop.sys, "frozen", True, create=True), \
             mock.patch.dict(desktop.os.environ, {"YTDUO_SHELL_HELPER": "C:\\App Folder\\YT-Duo-Shell.exe"}), \
             mock.patch.object(desktop.os.path, "isfile", return_value=True), \
             mock.patch.object(desktop, "shell_environment"), \
             mock.patch.object(desktop.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            desktop.open_folder_windows("C:\\Music Folder")
        self.assertEqual(run.call_args.args[0],
                         ["C:\\App Folder\\YT-Duo-Shell.exe", "folder", "C:\\Music Folder"])
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_rounding_does_not_reallocate_region_while_moving(self):
        chrome = desktop.WindowChrome.__new__(desktop.WindowChrome)
        chrome.root, chrome.user, chrome.gdi = mock.Mock(), mock.Mock(), mock.Mock()
        chrome.hwnd, chrome._rounded_size = 123, None
        def rect(hwnd, pointer):
            r = pointer._obj
            r.left, r.top, r.right, r.bottom = 10, 20, 650, 420
            return 1
        chrome.user.GetWindowRect.side_effect = rect
        chrome.gdi.CreateRoundRectRgn.return_value = 456
        chrome.round_corners()
        chrome.round_corners()
        chrome.gdi.CreateRoundRectRgn.assert_called_once_with(0, 0, 641, 401, 24, 24)
        chrome.user.SetWindowRgn.assert_called_once_with(123, 456, True)
        chrome.gdi.DeleteObject.assert_not_called()
