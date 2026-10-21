"""Updater integrity, redirect boundaries, platform selection and handoff."""
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import urllib.request
from ytduo import updates

class DirectUpdates(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.target = self.root / "YT-Duo.exe"
        self.target.write_bytes(b"old app")
        self.environment = mock.patch.dict(os.environ, {"YTDUO_LAUNCHER": str(self.target)})
        self.environment.start()
    def tearDown(self):
        self.environment.stop()
        self.folder.cleanup()
    def opener(self, body=b"new app", digest=None, prefix="windows/", name="YT-Duo-4.6.4-Portable.exe"):
        digest = digest or hashlib.sha256(body).hexdigest()
        opener = mock.Mock()
        opener.open.side_effect = [io.BytesIO((digest+"  "+prefix+name+"\n").encode()), io.BytesIO(body)]
        return opener
    def test_verified_asset_and_progress_without_changing_running_app(self):
        progress = mock.Mock()
        opener = self.opener()
        with mock.patch.object(updates.sys, "platform", "win32"):
            stage = Path(updates.download_update("4.6.4", progress, opener))
        try:
            self.assertEqual(stage.read_bytes(), b"new app")
            self.assertEqual(self.target.read_bytes(), b"old app")
            progress.assert_called_once_with(7)
            self.assertTrue(opener.open.call_args.args[0].full_url.endswith("/v4.6.4/YT-Duo-4.6.4-Portable.exe"))
        finally:
            shutil.rmtree(stage.parent)
    def test_corruption_is_rejected_and_staging_cleaned(self):
        with mock.patch.object(updates.sys, "platform", "win32"), mock.patch.object(
                updates.tempfile, "mkdtemp", return_value=str(self.root / "stage")):
            (self.root / "stage").mkdir()
            with self.assertRaisesRegex(ValueError, "checksum did not match"):
                updates.download_update("4.6.4", opener=self.opener(digest="0"*64))
            self.assertFalse((self.root / "stage").exists())
        self.assertEqual(self.target.read_bytes(), b"old app")
    def test_missing_checksum_never_downloads_executable(self):
        opener = mock.Mock()
        opener.open.return_value = io.BytesIO(b"not a checksum")
        with self.assertRaisesRegex(ValueError, "checksum"):
            updates.download_update("4.6.4", opener=opener)
        self.assertEqual(opener.open.call_count, 1)
    def test_unknown_launcher_and_invalid_version_never_request_network(self):
        opener = mock.Mock()
        for version in ("../evil", "4.6.4-beta"):
            with self.assertRaises(ValueError):
                updates.download_update(version, opener=opener)
        with mock.patch.dict(os.environ, {"YTDUO_LAUNCHER": ""}):
            with self.assertRaises(OSError):
                updates.download_update("4.6.4", opener=opener)
        opener.open.assert_not_called()
    def test_redirect_rejects_other_hosts_and_downgrade(self):
        redirect = updates.AssetRedirect()
        for url in ("http://github.com/file", "https://evil.invalid/file", "https://github.com.evil.invalid/file"):
            with self.assertRaises(ValueError):
                redirect.redirect_request(urllib.request.Request(updates.REPO_URL), None, 302, "", {}, url)
    def test_windows_handoff_waits_for_python_and_launcher(self):
        stage = self.root / "new.exe"
        stage.write_bytes(b"new")
        with mock.patch.object(updates.sys, "platform", "win32"), mock.patch.dict(
                os.environ, {"YTDUO_LAUNCHER_PID": "123"}), mock.patch.object(updates.subprocess, "Popen") as start:
            updates.apply_update(stage)
        self.assertEqual(start.call_args.args[0], [str(stage), "--apply-update", str(self.target), str(os.getpid()), "123"])
        self.assertEqual(self.target.read_bytes(), b"old app")
    def test_linux_and_macos_select_correct_launchers(self):
        for platform, filename, prefix in (("linux", "YT-Duo.sh", "linux/"), ("darwin", "YT-Duo.command", "macos/")):
            with mock.patch.object(updates.sys, "platform", platform):
                stage = Path(updates.download_update("4.6.4", opener=self.opener(name=filename, prefix=prefix)))
            self.assertEqual(stage.name, filename)
            shutil.rmtree(stage.parent)
    @unittest.skipIf(os.name == "nt", "Unix permissions and process handoff")
    def test_unix_replace_keeps_mode_and_restarts_exact_launcher(self):
        stage_dir = self.root / "stage"
        stage_dir.mkdir()
        stage = stage_dir / "YT-Duo.sh"
        stage.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.target.chmod(0o755)
        with mock.patch.object(updates.subprocess, "Popen") as start:
            updates.apply_update(stage)
        self.assertEqual(self.target.read_bytes(), b"#!/bin/sh\nexit 0\n")
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o755)
        self.assertEqual(start.call_args.args[0], ["/bin/sh", str(self.target)])
        self.assertFalse(stage_dir.exists())

    @unittest.skipUnless(os.name == "nt", "Windows replacement helper")
    def test_real_windows_helper_replaces_existing_exe_and_relaunches(self):
        import time
        compiler = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        source = Path(__file__).resolve().parents[1] / "scripts/windows_launcher.cs"
        harness = self.root / "UpdateTest.cs"
        harness.write_text('''using System;
using System.IO;
using System.Reflection;
class UpdateTest {
    static int Main(string[] args) {
        if (args.Length == 0) {
            File.WriteAllText(Path.Combine(Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location), "restarted.txt"), "ok");
            return 0;
        }
        MethodInfo apply = typeof(Launcher).GetMethod("ApplyUpdate", BindingFlags.Static | BindingFlags.NonPublic);
        return (int)apply.Invoke(null, new object[] { new string[] { "--apply-update", args[0], "0", "0" } });
    }
}
''', encoding="utf-8")
        binary = self.root / "new.exe"
        subprocess.run([str(compiler), "/nologo", "/main:UpdateTest", "/reference:System.Windows.Forms.dll",
            "/reference:System.IO.Compression.dll", "/out:"+str(binary), str(source), str(harness)],
            check=True, capture_output=True, timeout=30)
        result = subprocess.run([str(binary), str(self.target)], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        for _ in range(100):
            if (self.root / "restarted.txt").exists(): break
            time.sleep(.05)
        self.assertTrue((self.root / "restarted.txt").exists())
        self.assertEqual(self.target.read_bytes(), binary.read_bytes())
        self.assertFalse(Path(str(self.target)+".previous").exists())

    @unittest.skipIf(os.name == "nt", "Unix rollback")
    def test_failed_unix_restart_restores_old_launcher(self):
        stage_dir = self.root / "stage"
        stage_dir.mkdir()
        stage = stage_dir / "YT-Duo.sh"
        stage.write_bytes(b"new app")
        with mock.patch.object(updates.subprocess, "Popen", side_effect=OSError("launch failed")):
            with self.assertRaises(OSError):
                updates.apply_update(stage)
        self.assertEqual(self.target.read_bytes(), b"old app")
        self.assertEqual(list(self.root.glob(".ytduo-*")), [])
