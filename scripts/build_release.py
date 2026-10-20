"""Build compact local releases: Unix launchers and optional Windows exe."""
import argparse
import base64
import hashlib
import io
import os
import shutil
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "Releases"


def publish_artifact(source, destination):
    """Rename a completed same-volume artifact; retain its build descriptor.

    Compiling/copying straight into an inherited Low-integrity output folder
    can make Windows launch the EXE at Low integrity. Do not edit ACLs or
    integrity labels; preserve the normally built file by an atomic rename.
    """
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.stat().st_dev != destination.parent.stat().st_dev:
        raise RuntimeError("Build and release folders must be on the same volume for atomic publication.")
    source.replace(destination)


def build_windows(output):
    if sys.platform != "win32":
        raise RuntimeError("Build the Windows executable on Windows.")
    bundle = ROOT / "build/windows-bundle"
    compiler = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    helper = ROOT / "build/YT-Duo-Shell.exe"
    helper.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(compiler), "/nologo", "/target:exe", "/platform:x64",
                    "/out:" + str(helper), str(ROOT / "scripts/windows_shell.cs")], check=True)
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir",
        "--windowed", "--noupx", "--name", "YT-Duo",
        "--icon", str(ROOT / "src/ytduo/assets/icon.ico"),
        "--add-data", str(ROOT / "src/ytduo/assets") + os.pathsep + "ytduo/assets",
        "--paths", str(ROOT / "src"), "--hidden-import", "tkinter.ttk",
        "--exclude-module", "pytest", "--exclude-module", "numpy",
        "--exclude-module", "pandas", "--exclude-module", "matplotlib",
        "--distpath", str(bundle), "--workpath", str(ROOT / "build/pyinstaller"),
        "--specpath", str(ROOT / "build"), str(ROOT / "src/ytduo/_entry.py"),
    ], cwd=ROOT, check=True)
    payload = ROOT / "build/app.zip"
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((bundle / "YT-Duo").rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(bundle / "YT-Duo").as_posix())
    compiler = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run([
        str(compiler), "/nologo", "/target:winexe", "/platform:x64",
        "/reference:System.Windows.Forms.dll", "/reference:System.IO.Compression.dll",
        "/win32icon:" + str(ROOT / "src/ytduo/assets/icon.ico"),
        "/out:" + str(output / "YT-Duo-Portable.exe"),
        "/resource:" + str(payload) + ",app.zip",
        "/resource:" + str(helper) + ",shell.exe",
        str(ROOT / "scripts/windows_launcher.cs"),
    ], cwd=ROOT, check=True)

