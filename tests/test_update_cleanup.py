"""Native maintenance deletes only owned, inactive updater artifacts."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

@unittest.skipUnless(os.name == "nt", "Windows launcher maintenance")
class NativeMaintenance(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="ytduo-maintenance-test-")
        self.root = Path(self.folder.name).resolve()
        self.addCleanup(self.folder.cleanup)
        source = self.root / "Maintenance.cs"
        source.write_text('''using System;
using System.IO;
using System.Reflection;
class Maintenance {
    static void Invoke(string method, params object[] args) {
        typeof(Launcher).GetMethod(method, BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, args);
    }
    static int Main(string[] args) {
        if (args[0] == "cleanup") { Invoke("CleanupUpdateStage"); return 0; }
        string cache = args[1];
        string current = Path.Combine(cache, new string('a',64));
        string old = Path.Combine(cache, new string('b',64));
        string live = Path.Combine(cache, new string('c',64));
        using (FileStream lease = new FileStream(Path.Combine(live,"in-use.lock"), FileMode.Open, FileAccess.Read, FileShare.Read)) {
            Invoke("PruneIdleRuntimes",cache,current);
            Invoke("PruneShellHelpers",cache,Path.Combine(cache,"shell",new string('d',64),"helper.exe"),current);
            if (!Directory.Exists(Path.Combine(cache,"shell",new string('e',64)))) return 4;
            if (!Directory.Exists(current) || Directory.Exists(old) || !Directory.Exists(live)) return 2;
        }
        Invoke("PruneIdleRuntimes",cache,current);
        if (Directory.Exists(live) || !Directory.Exists(Path.Combine(cache,"user-data"))) return 3;
        Invoke("PruneShellHelpers",cache,Path.Combine(cache,"shell",new string('d',64),"helper.exe"),current);
        if (Directory.Exists(Path.Combine(cache,"shell",new string('e',64)))) return 5;
        return 0;
    }
}
''',encoding="utf-8")
        self.executable = self.root / "Maintenance.exe"
        compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        launcher = Path(__file__).resolve().parents[1] / "scripts/windows_launcher.cs"
        subprocess.run([str(compiler),"/nologo","/main:Maintenance","/reference:System.Windows.Forms.dll",
            "/reference:System.IO.Compression.dll","/out:"+str(self.executable),str(launcher),str(source)],
            check=True,capture_output=True,timeout=30)
    def test_all_idle_runtimes_removed_but_live_and_unknown_directories_preserved(self):
        cache = self.root / "cache"
        for name in ("a"*64,"b"*64,"c"*64,"user-data"):
            folder = cache/name;folder.mkdir(parents=True)
            (folder/"in-use.lock").touch()
        for name in ("d"*64,"e"*64,"user-data"):
            folder=cache/"shell"/name;folder.mkdir(parents=True)
            (folder/"helper.exe").write_bytes(b"helper")
        subprocess.run([str(self.executable),"prune",str(cache)],check=True,timeout=20)
    def test_update_stage_is_removed_only_inside_owned_temp_directory(self):
        with tempfile.TemporaryDirectory(prefix="ytduo-update-") as stage:
            source=Path(stage)/"YT-Duo-99.0.0-Portable.exe";source.write_bytes(b"stage")
            keep=Path(stage)/"keep.txt";keep.write_text("user data")
            env=dict(os.environ,YTDUO_UPDATE_STAGE=str(source),YTDUO_UPDATE_HELPER_PID="0")
            subprocess.run([str(self.executable),"cleanup"],env=env,check=True,timeout=20)
            self.assertFalse(source.exists())
            self.assertEqual(keep.read_text(),"user data")
        outside=self.root/"YT-Duo-99.0.0-Portable.exe";outside.write_bytes(b"preserve")
        env=dict(os.environ,YTDUO_UPDATE_STAGE=str(outside),YTDUO_UPDATE_HELPER_PID="0")
        subprocess.run([str(self.executable),"cleanup"],env=env,check=True,timeout=20)
        self.assertEqual(outside.read_bytes(),b"preserve")
