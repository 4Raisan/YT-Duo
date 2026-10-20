"""Dependency resolution and release input checks; no network or installers."""
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

from ytduo import dependencies, engine
from ytduo.config import Settings
from scripts import build_release


class DependencyTests(unittest.TestCase):
    def test_bundled_tools_work_without_path_and_explicit_setting_wins(self):
        with tempfile.TemporaryDirectory() as folder:
            tools = Path(folder) / "tools"
            tools.mkdir()
            for name in ("ffmpeg", "ffprobe", "deno"):
                (tools / (name + ".exe")).write_bytes(b"tool")
            with mock.patch.object(dependencies.sys, "frozen", True, create=True), \
                 mock.patch.object(dependencies.sys, "_MEIPASS", folder, create=True), \
                 mock.patch.object(dependencies.sys, "platform", "win32"), \
                 mock.patch.object(dependencies.shutil, "which", return_value=None):
                self.assertEqual(engine.find_ffmpeg(), str(tools / "ffmpeg.exe"))
                self.assertEqual(engine.find_ffprobe(), str(tools / "ffprobe.exe"))
                self.assertEqual(dependencies.find_deno(), str(tools / "deno.exe"))
                settings = Settings(ffmpeg_path="custom-ffmpeg", ffprobe_path="custom-ffprobe")
                self.assertEqual(engine.find_ffmpeg(settings), "custom-ffmpeg")
                self.assertEqual(engine.find_ffprobe(settings), "custom-ffprobe")

    def test_source_uses_system_deno_without_changing_environment(self):
        with mock.patch.object(dependencies.sys, "frozen", False, create=True), \
             mock.patch.object(dependencies.shutil, "which", return_value="/tools/deno"):
            self.assertEqual(dependencies.find_deno(), "/tools/deno")

    def test_yt_dlp_receives_deno_path(self):
        with mock.patch.object(dependencies, "find_deno", return_value="/private/tools/deno"):
            opts = engine._base_opts(Settings(), engine.EngineLogger())
        self.assertEqual(opts["js_runtimes"], {"deno": {"path": "/private/tools/deno"}})

    def test_report_marks_missing_or_unrunnable_tools(self):
        with mock.patch.object(engine, "find_ffmpeg", return_value="bad-tool"), \
             mock.patch.object(engine, "find_ffprobe", return_value=None), \
             mock.patch.object(dependencies, "find_deno", return_value=None), \
             mock.patch.object(dependencies.subprocess, "run", side_effect=OSError("blocked")):
            rows = {name: ok for name, ok, _detail in dependencies.report()}
        self.assertFalse(rows["FFmpeg"])
        self.assertFalse(rows["FFprobe"])
        self.assertFalse(rows["Deno"])

    def test_cli_dependency_check_returns_failure_without_starting_gui(self):
        from ytduo.cli import main
        with mock.patch.object(dependencies, "report", return_value=[("FFprobe", False, "missing")]), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main(["--check-dependencies"]), 1)
        self.assertIn("FFprobe", output.getvalue())

