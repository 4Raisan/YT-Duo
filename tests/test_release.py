"""Offline checks for embedded launchers and their argument forwarding."""
from pathlib import Path
import ast
import base64
import io
import os
import re
import zipfile
import subprocess
import sys
import tempfile
import unittest

from scripts import build_release
from ytduo import __version__


class StandaloneRelease(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows compression and launcher")
    def test_native_payload_roundtrip_and_corrupt_input_in_real_launcher_decoder(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            raw = (b"YT-Duo payload roundtrip\x00" * 50000) + bytes(range(256))
            (root / "input.bin").write_bytes(raw)
            build_release.compress_windows_payload(root / "input.bin", root / "app.lzms")
            self.assertLess((root / "app.lzms").stat().st_size, len(raw) // 2)
            harness = root / "PayloadTest.cs"
            harness.write_text('''using System;
using System.IO;
using System.Reflection;
class PayloadTest {
    static int Main(string[] args) {
        MethodInfo decode = typeof(Launcher).GetMethod("ExpandPayload", BindingFlags.Static | BindingFlags.NonPublic);
        byte[] result = (byte[])decode.Invoke(null, new object[] { File.ReadAllBytes(args[0]) });
        File.WriteAllBytes(args[1], result);
        try { decode.Invoke(null, new object[] { new byte[40] }); return 2; }
        catch (TargetInvocationException e) { if (!(e.InnerException is InvalidDataException)) return 3; }
        return 0;
    }
}''')
            compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
            subprocess.run([str(compiler), "/nologo", "/target:exe", "/main:PayloadTest",
                "/out:" + str(root / "test.exe"), "/reference:System.Windows.Forms.dll",
                "/reference:System.IO.Compression.dll", str(harness),
                str(build_release.ROOT / "scripts/windows_launcher.cs")], check=True, capture_output=True)
            subprocess.run([str(root / "test.exe"), str(root / "app.lzms"),
                            str(root / "decoded.bin")], check=True, capture_output=True, timeout=30)
            self.assertEqual((root / "decoded.bin").read_bytes(), raw)

    def test_publication_moves_the_built_file_instead_of_copying_into_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "stage").mkdir()
            (root / "release").mkdir()
            source, target = root / "stage/app.exe", root / "release/app.exe"
            source.write_bytes(b"new executable")
            target.write_bytes(b"previous executable")
            identity = source.stat().st_ino
            build_release.publish_artifact(source, target)
            self.assertFalse(source.exists())
            self.assertEqual(target.read_bytes(), b"new executable")
            self.assertEqual(target.stat().st_ino, identity)

    def test_unix_payload_contains_the_application_icon(self):
        with tempfile.TemporaryDirectory() as folder:
            build_release.build_unix(Path(folder))
            shell = (Path(folder) / "YT-Duo.sh").read_text(encoding="utf-8")
            literal = re.search(r"base64.b64decode\(('[A-Za-z0-9+/=]+')\)", shell).group(1)
            with zipfile.ZipFile(io.BytesIO(base64.b64decode(ast.literal_eval(literal)))) as payload:
                self.assertTrue(payload.read("ytduo/assets/icon.png").startswith(b"\x89PNG"))

    def test_checksums_exclude_runtime_executables(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            (output / "windows/.ytduo-runtime").mkdir(parents=True)
            (output / ("windows/YT-Duo-" + __version__ + "-Portable.exe")).write_bytes(b"portable")
            (output / "windows/.ytduo-runtime/old.exe").write_bytes(b"runtime")
            build_release.checksums(output)
            manifest = (output / "SHA256SUMS.txt").read_text()
            self.assertIn(("windows/YT-Duo-" + __version__ + "-Portable.exe"), manifest)
            self.assertNotIn("old.exe", manifest)

    def run_embedded(self, argument):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            build_release.build_unix(output)
            shell = (output / "YT-Duo.sh").read_text(encoding="utf-8")
            bootstrap = shell.split("<<'YTDUO_SOURCE'\n", 1)[1].rsplit("YTDUO_SOURCE\n", 1)[0]
            return subprocess.run([sys.executable, "-", argument], input=bootstrap,
                                  text=True, capture_output=True, cwd=folder,
                                  timeout=30)

    def test_version_works_away_from_checkout(self):
        result = self.run_embedded("--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("YT-Duo " + __version__, result.stdout)

    def test_invalid_arguments_keep_exit_status(self):
        result = self.run_embedded("--invalid-option")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("unrecognized arguments", result.stderr)
