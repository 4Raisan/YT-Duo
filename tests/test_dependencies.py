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


class BuildInputTests(unittest.TestCase):
    def archive(self, files):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as package:
            for name, value in files:
                package.writestr(name, value)
        stream.seek(0)
        return stream

    def test_selected_binaries_dlls_and_license_are_kept_not_ffplay(self):
        data = self.archive([("dist/bin/ffmpeg.exe", b"a"), ("dist/bin/ffprobe.exe", b"b"),
                             ("dist/bin/avcodec.dll", b"dll"), ("dist/LICENSE.txt", b"license"),
                             ("dist/bin/ffplay.exe", b"unused")])
        with tempfile.TemporaryDirectory() as folder:
            build_release.extract_tools(data, folder, ["ffmpeg.exe", "ffprobe.exe"], "ffmpeg")
            root = Path(folder)
            self.assertEqual((root / "avcodec.dll").read_bytes(), b"dll")
            self.assertTrue((root / "licenses/ffmpeg/dist/LICENSE.txt").is_file())
            self.assertFalse((root / "ffplay.exe").exists())

    def test_missing_ffprobe_rejects_incomplete_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, "Missing"):
                build_release.extract_tools(self.archive([("bin/ffmpeg.exe", b"a")]),
                    folder, ["ffmpeg.exe", "ffprobe.exe"], "ffmpeg")

    def test_archive_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, "Unsafe"):
                build_release.extract_tools(self.archive([("../deno.exe", b"a")]),
                                            folder, ["deno.exe"], "deno")

    def test_duplicate_binary_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, "Duplicate"):
                build_release.extract_tools(self.archive([("a/deno.exe", b"a"),
                    ("b/deno.exe", b"b")]), folder, ["deno.exe"], "deno")

    def test_checksum_failure_never_publishes_download(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "tool.zip"
            with mock.patch.object(build_release.urllib.request, "urlopen", return_value=io.BytesIO(b"bad")):
                with self.assertRaisesRegex(RuntimeError, "checksum"):
                    build_release.verified_download("https://example.test/tool.zip", "0" * 64, target)
            self.assertFalse(target.exists())

    def test_verified_cache_does_not_download(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "tool.zip"
            target.write_bytes(b"verified")
            with mock.patch.object(build_release.urllib.request, "urlopen") as network:
                build_release.verified_download("https://example.test/tool.zip",
                    hashlib.sha256(b"verified").hexdigest(), target)
                network.assert_not_called()

    def test_source_bundle_has_license_and_no_media_or_binaries(self):
        with tempfile.TemporaryDirectory() as folder:
            name = build_release.build_source(folder)
            with zipfile.ZipFile(Path(folder) / name) as package:
                names = package.namelist()
                self.assertIn("YT-Duo/LICENSE", names)
                self.assertIn("YT-Duo/pyproject.toml", names)
                self.assertFalse(any(n.endswith((".exe", ".mkv", ".opus")) for n in names))
