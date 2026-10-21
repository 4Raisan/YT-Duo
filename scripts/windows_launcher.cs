// Single-download launcher. Uses inherited user-cache permissions rather than
// PyInstaller's restricted one-file temporary-directory security descriptor.
using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Security.Cryptography;
using System.Threading;
using System.Runtime.InteropServices;
using System.Windows.Forms;

[assembly: AssemblyTitle("YT-Duo")]
[assembly: AssemblyDescription("Download YouTube videos in the best available quality.")]
[assembly: AssemblyProduct("YT-Duo")]
[assembly: AssemblyCompany("4Raisan")]
[assembly: AssemblyVersion("4.6.4.0")]
[assembly: AssemblyFileVersion("4.6.4.0")]

class Launcher {
    static void WaitForProcess(int id) {
        if (id <= 0) return;
        try {
            using (Process process = Process.GetProcessById(id)) {
                if (!process.WaitForExit(60000))
                    throw new IOException("Close the old YT-Duo windows and try again.");
            }
        } catch (ArgumentException) { } // Already exited.
    }

    static int ApplyUpdate(string[] args) {
        string target = Path.GetFullPath(args[1]);
        string source = Assembly.GetExecutingAssembly().Location;
        if (!target.EndsWith(".exe", StringComparison.OrdinalIgnoreCase) ||
            !File.Exists(target) || String.Equals(target, source, StringComparison.OrdinalIgnoreCase))
            throw new IOException("Invalid update destination.");
        WaitForProcess(Int32.Parse(args[2]));
        WaitForProcess(Int32.Parse(args[3]));
        string backup = target + ".previous";
        if (File.Exists(backup))
            throw new IOException("A previous update backup exists: " + backup);
        string stage = target + ".update-" + Guid.NewGuid().ToString("N");
        string copy = source + ".copy";
        try {
            File.Copy(source, copy);
            // Move on the same volume preserves the normal downloaded file's identity.
            // Cross-volume copies use a same-folder stage for atomic replacement.
            if (Path.GetPathRoot(copy) == Path.GetPathRoot(stage)) File.Move(copy, stage);
            else { File.Copy(copy, stage); File.Delete(copy); }
            File.Replace(stage, target, backup);
            try {
                ProcessStartInfo start = new ProcessStartInfo(target);
                start.UseShellExecute = false;
                start.WorkingDirectory = Path.GetDirectoryName(target);
                start.EnvironmentVariables.Remove("YTDUO_LAUNCHER_PID");
                using (Process child = Process.Start(start)) { }
            } catch {
                File.Replace(backup, target, null);
                throw;
            }
            File.Delete(backup);
            return 0;
        } finally {
            if (File.Exists(stage)) File.Delete(stage);
            if (File.Exists(copy)) File.Delete(copy);
            // The updater is still executing; retain only this temporary EXE until Temp cleanup.
        }
    }
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport("cabinet.dll", SetLastError = true)]
    static extern bool CreateDecompressor(uint algorithm, IntPtr allocator, out IntPtr handle);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport("cabinet.dll", SetLastError = true)]
    static extern bool Decompress(IntPtr handle, byte[] input, UIntPtr inputSize,
        [Out] byte[] output, UIntPtr outputSize, out UIntPtr used);
    [DefaultDllImportSearchPaths(DllImportSearchPath.System32)]
    [DllImport("cabinet.dll")]
    static extern bool CloseDecompressor(IntPtr handle);

    static byte[] ExpandPayload(byte[] input) {
        IntPtr handle;
        if (!CreateDecompressor(5, IntPtr.Zero, out handle))
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        try {
            UIntPtr size;
            if (Decompress(handle, input, (UIntPtr)input.Length, null, UIntPtr.Zero, out size) ||
                Marshal.GetLastWin32Error() != 122 || size.ToUInt64() == 0 ||
                size.ToUInt64() > 1024UL * 1024 * 1024)
                throw new InvalidDataException("Invalid compressed application payload.");
            byte[] output = new byte[(int)size.ToUInt64()];
            UIntPtr used;
            if (!Decompress(handle, input, (UIntPtr)input.Length, output, (UIntPtr)output.Length, out used) ||
                used.ToUInt64() != (ulong)output.Length)
                throw new InvalidDataException("Could not decompress the application payload.");
            return output;
        } finally { CloseDecompressor(handle); }
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool SetDllDirectory(string path);

    static string CacheRoot() {
        string[] roots = { Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                           Environment.GetEnvironmentVariable("TEMP"),
                           Environment.GetEnvironmentVariable("TMP") };
        foreach (string root in roots) {
            if (String.IsNullOrWhiteSpace(root)) continue;
            try {
                string cache = Path.Combine(root, "YT-Duo", "runtime");
                Directory.CreateDirectory(cache);
                string probe = Path.Combine(cache, "write-" + Guid.NewGuid().ToString("N"));
                using (File.Create(probe)) {}
                File.Delete(probe);
                return cache;
            } catch (UnauthorizedAccessException) {} catch (IOException) {}
        }
        throw new IOException("Cannot create the runtime cache in your user profile or temporary folder. Check that those folders are writable.");
    }

    static byte[] Resource(string name) {
        using (Stream input = Assembly.GetExecutingAssembly().GetManifestResourceStream(name))
        using (MemoryStream data = new MemoryStream()) { input.CopyTo(data); return data.ToArray(); }
    }

    static string Digest(byte[] data) {
        using (SHA256 hash = SHA256.Create())
            return BitConverter.ToString(hash.ComputeHash(data)).Replace("-", "").ToLowerInvariant();
    }

    static string PrepareShell(string cache) {
        byte[] data = Resource("shell.exe");
        string folder = Path.Combine(cache, "shell", Digest(data));
        Directory.CreateDirectory(folder);
        string target = Path.Combine(folder, "YT-Duo-Shell.exe");
        if (!File.Exists(target)) {
            string stage = target + "." + Guid.NewGuid().ToString("N");
            File.WriteAllBytes(stage, data);
            try { File.Move(stage, target); }
            catch (IOException) { if (!File.Exists(target)) throw; File.Delete(stage); }
        }
        return target;
    }

    static void PruneIdleRuntimes(string cache, string current) {
        DirectoryInfo[] versions = new DirectoryInfo(cache).GetDirectories();
        Array.Sort(versions, delegate(DirectoryInfo a, DirectoryInfo b) {
            return b.LastWriteTimeUtc.CompareTo(a.LastWriteTimeUtc);
        });
        int retained = 0;
        foreach (DirectoryInfo version in versions) {
            if (!System.Text.RegularExpressions.Regex.IsMatch(version.Name, "^[a-f0-9]{64}$") ||
                (version.Attributes & FileAttributes.ReparsePoint) != 0) continue;
            if (version.FullName == current || retained++ < 1) continue;
            string lease = Path.Combine(version.FullName, "in-use.lock");
            // Legacy caches without a lease may belong to an older running app.
            if (!File.Exists(lease)) continue;
            try {
                using (FileStream test = new FileStream(lease, FileMode.Open,
                       FileAccess.ReadWrite, FileShare.None)) {}
                // Called under extract.lock; another launcher cannot race us.
                Directory.Delete(version.FullName, true);
            } catch (IOException) {} catch (UnauthorizedAccessException) {}
        }
    }

    [STAThread]
    static int Main(string[] args) {
        try {
            // A frozen Python parent may pass its private DLL search directory.
            // Reset only this helper process before replacement or relaunch.
            SetDllDirectory(null);
            if (args.Length == 4 && args[0] == "--apply-update") return ApplyUpdate(args);
            string cache = CacheRoot();
            string shellHelper = PrepareShell(cache);
            if (args.Length == 2 && (args[0] == "--open-folder" || args[0] == "--open-url")) {
                SetDllDirectory(null);
                ProcessStartInfo action = new ProcessStartInfo(shellHelper);
                action.UseShellExecute = false; action.CreateNoWindow = true;
                action.Arguments = (args[0] == "--open-folder" ? "folder " : "url ") +
                    "\"" + args[1].Replace("\"", "") + "\"";
                using (Process helper = Process.Start(action)) { helper.WaitForExit(); return helper.ExitCode; }
            }
            byte[] payload = Resource("app.lzms");
            string digest = Digest(payload);
            string target = Path.Combine(cache, digest);
            string executable = Path.Combine(target, "YT-Duo.exe");
            FileStream gate = null;
            FileStream lease = null;
            for (int attempt = 0; attempt < 300 && gate == null; attempt++) {
                try { gate = new FileStream(Path.Combine(cache, "extract.lock"),
                    FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None); }
                catch (IOException) { Thread.Sleep(100); }
            }
            if (gate == null) throw new IOException("Another startup is still extracting. Try again shortly.");
            using (gate) {
                if (!Directory.Exists(target)) {
                    string stage = Path.Combine(cache, "stage-" + Guid.NewGuid().ToString("N"));
                    Directory.CreateDirectory(stage);
                    try {
                        string prefix = Path.GetFullPath(stage) + Path.DirectorySeparatorChar;
                        using (MemoryStream data = new MemoryStream(ExpandPayload(payload)))
                        using (ZipArchive archive = new ZipArchive(data, ZipArchiveMode.Read)) {
                            foreach (ZipArchiveEntry entry in archive.Entries) {
                                string destination = Path.GetFullPath(Path.Combine(stage, entry.FullName));
                                if (!destination.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                                    throw new InvalidDataException("Invalid bundled path.");
                                if (entry.Name.Length == 0) { Directory.CreateDirectory(destination); continue; }
                                Directory.CreateDirectory(Path.GetDirectoryName(destination));
                                using (Stream input = entry.Open())
                                using (FileStream output = File.Create(destination)) input.CopyTo(output);
                            }
                        }
                        if (!File.Exists(Path.Combine(stage, "YT-Duo.exe")))
                            throw new InvalidDataException("The application payload is incomplete.");
                        Directory.Move(stage, target);
                    } finally {
                        if (Directory.Exists(stage)) Directory.Delete(stage, true);
                    }
                }
                string leasePath = Path.Combine(target, "in-use.lock");
                if (!File.Exists(leasePath)) using (File.Create(leasePath)) {}
                lease = new FileStream(leasePath, FileMode.Open, FileAccess.Read, FileShare.Read);
                PruneIdleRuntimes(cache, target);
            }
            if (!File.Exists(executable)) throw new IOException("The cached app is missing. Remove its runtime cache and retry.");
            ProcessStartInfo start = new ProcessStartInfo(executable);
            start.UseShellExecute = false;
            start.WorkingDirectory = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            if (String.IsNullOrEmpty(Environment.GetEnvironmentVariable("YTDUO_HOME")))
                start.EnvironmentVariables["YTDUO_HOME"] = File.Exists(Path.Combine(
                    start.WorkingDirectory, "installed.txt")) ? Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "YT-Duo", "data") : start.WorkingDirectory;
            start.EnvironmentVariables["YTDUO_INSTALLED"] = File.Exists(Path.Combine(start.WorkingDirectory, "installed.txt")) ? "1" : "0";
            start.EnvironmentVariables["YTDUO_LAUNCHER"] = Assembly.GetExecutingAssembly().Location;
            start.EnvironmentVariables["YTDUO_LAUNCHER_PID"] = Process.GetCurrentProcess().Id.ToString();
            start.EnvironmentVariables["YTDUO_SHELL_HELPER"] = shellHelper;
            using (lease)
            using (Process child = Process.Start(start)) {
                child.WaitForExit();
                return child.ExitCode;
            }
        } catch (Exception error) {
            MessageBox.Show("YT-Duo could not start.\n\n" + error.Message,
                "YT-Duo", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
