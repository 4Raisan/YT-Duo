"""Find bundled tools without changing PATH or installing software at runtime."""
import importlib.util
from importlib import metadata, resources
import os
from pathlib import Path
import shutil
import subprocess
import sys


def bundled_tool(name):
    if not getattr(sys, "frozen", False):
        return None
    root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    candidate = root / "tools" / (name + (".exe" if sys.platform == "win32" else ""))
    return str(candidate) if candidate.is_file() else None


def find_deno():
    return bundled_tool("deno") or shutil.which("deno")


def report(settings=None):
    from . import engine
    from yt_dlp.version import __version__ as yt_version
    try:
        scripts = resources.files("yt_dlp_ejs").joinpath("yt", "solver")
        ejs_ok = all(scripts.joinpath(name).is_file() for name in ("core.min.js", "lib.min.js"))
        ejs_version = metadata.version("yt-dlp-ejs")
    except (ImportError, OSError):
        ejs_ok, ejs_version = False, "not installed"
    try:
        import tkinter
        tk_ok, tk_version = True, str(tkinter.TkVersion)
    except (ImportError, OSError):
        tk_ok, tk_version = False, "not installed"
    rows = [("Python", sys.version_info >= (3, 10), sys.version.split()[0]),
            ("yt-dlp", True, yt_version),
            ("YouTube JS helpers", ejs_ok, ejs_version),
            ("Tkinter", tk_ok, tk_version)]
    for label, path, flag in (("FFmpeg", engine.find_ffmpeg(settings), "-version"),
                              ("FFprobe", engine.find_ffprobe(settings), "-version"),
                              ("Deno", find_deno(), "--version")):
        ok, detail = False, "not found"
        if path:
            try:
                result = subprocess.run([path, flag], capture_output=True, text=True,
                    errors="replace", timeout=10,
                    creationflags=0x08000000 if os.name == "nt" else 0)
                ok = result.returncode == 0
                lines = (result.stdout or result.stderr).splitlines()
                detail = (lines[0] if lines else "could not run") + " — " + path
            except (OSError, subprocess.TimeoutExpired) as exc:
                detail = str(exc)
        rows.append((label, ok, detail))
    return rows


SETUP_HELP = ("Windows portable/Setup includes these tools; re-download the full app if one is missing.\n"
              "Source/Linux/macOS: see https://github.com/4Raisan/YT-Duo/blob/main/Releases/README.md")
