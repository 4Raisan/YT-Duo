// Small unfrozen desktop helper. Never loads Python or changes its DLL state.
using System;
using System.IO;
using System.Runtime.InteropServices;

class DesktopShell {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode)]
    static extern bool SetDllDirectory(string path);
    [DllImport("ole32.dll")] static extern int CoInitializeEx(IntPtr reserved, uint mode);
    [DllImport("ole32.dll")] static extern void CoUninitialize();
    [DllImport("ole32.dll")] static extern void CoTaskMemFree(IntPtr pointer);
    [DllImport("shell32.dll", CharSet=CharSet.Unicode)]
    static extern int SHParseDisplayName(string name, IntPtr context, out IntPtr pidl,
                                        uint attributes, IntPtr result);
    [DllImport("shell32.dll")]
    static extern int SHOpenFolderAndSelectItems(IntPtr pidl, uint count,
                                               IntPtr[] children, uint flags);
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
    struct ShellInfo {
        public int size; public uint mask; public IntPtr window;
        public string verb, file, parameters, directory; public int show;
        public IntPtr instance, idList; public string className;
        public IntPtr classKey; public uint hotKey;
        public IntPtr icon, process;
    }
    [DllImport("shell32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool ShellExecuteEx(ref ShellInfo info);

    [STAThread]
    static int Main(string[] args) {
        if (args.Length != 2) return 87;
        SetDllDirectory(null);
        int initialized = CoInitializeEx(IntPtr.Zero, 2);
        try {
            if (args[0] == "folder") {
                string folder = Path.GetFullPath(args[1]);
                if (!Directory.Exists(folder)) return 3;
                IntPtr pidl = IntPtr.Zero, child = IntPtr.Zero;
                try {
                    int result = SHParseDisplayName(folder, IntPtr.Zero, out pidl, 0, IntPtr.Zero);
                    if (result < 0) return result;
                    child = Marshal.AllocCoTaskMem(2);
                    Marshal.WriteInt16(child, 0);
                    return SHOpenFolderAndSelectItems(pidl, 1, new IntPtr[] {child}, 0);
                } finally {
                    if (pidl != IntPtr.Zero) CoTaskMemFree(pidl);
                    if (child != IntPtr.Zero) Marshal.FreeCoTaskMem(child);
                }
            }
            if (args[0] == "url") {
                Uri url;
                if (!Uri.TryCreate(args[1], UriKind.Absolute, out url) ||
                    url.Scheme != "https" || url.Host != "github.com" ||
                    !(url.AbsolutePath == "/4Raisan/YT-Duo" ||
                      url.AbsolutePath.StartsWith("/4Raisan/YT-Duo/", StringComparison.Ordinal))) return 87;
                ShellInfo info = new ShellInfo();
                info.size = Marshal.SizeOf(info);
                info.mask = 0x00000100 | 0x00000400; // complete activation; no native error dialogs
                info.verb = "open"; info.file = url.AbsoluteUri; info.show = 1;
                info.directory = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
                if (!ShellExecuteEx(ref info)) return Marshal.GetLastWin32Error();
                return 0;
            }
            return 87;
        } catch (Exception error) {
            return error is System.ComponentModel.Win32Exception ?
                ((System.ComponentModel.Win32Exception)error).NativeErrorCode : 1;
        } finally { if (initialized >= 0) CoUninitialize(); }
    }
}
