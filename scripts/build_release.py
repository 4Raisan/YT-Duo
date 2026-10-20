"""Build compact local releases: Unix launchers and optional Windows exe."""
import argparse
import base64
import hashlib
import io
import json
import os
import re
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "Releases"


def version():
    return re.search(r'__version__ = "([^"]+)"',
                     (ROOT / "src/ytduo/__init__.py").read_text()).group(1)


def verified_download(url, expected, destination):
    """Only the pinned build inputs are fetched, never during app startup."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == expected:
        return destination
    request = urllib.request.Request(url, headers={"User-Agent": "YT-Duo-release-builder"})
    staging = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=120) as response, staging.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise RuntimeError("Dependency checksum mismatch: " + destination.name)
    staging.replace(destination)
    return destination


def extract_tools(archive, destination, binaries, component):
    """Extract selected files without trusting archive paths or duplicate names."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    found = set()
    with zipfile.ZipFile(archive) as package:
        for entry in package.infolist():
            parts = entry.filename.replace("\\", "/").split("/")
            if ".." in parts or entry.filename.startswith(("/", "\\")) or ":" in parts[0]:
                raise RuntimeError("Unsafe dependency archive path")
            name = parts[-1]
            if name in binaries:
                if name in found:
                    raise RuntimeError("Duplicate dependency binary: " + name)
                (destination / name).write_bytes(package.read(entry))
                found.add(name)
            elif component == "ffmpeg" and len(parts) > 1 and parts[-2] == "bin" and name.endswith(".dll"):
                (destination / name).write_bytes(package.read(entry))
            elif not entry.is_dir() and (name.lower().startswith(("license", "copying", "readme"))
                                        or name.lower().endswith(".txt")):
                target = destination / "licenses" / component / Path(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(package.read(entry))
    if found != set(binaries):
        raise RuntimeError("Missing dependency binaries: " + ", ".join(set(binaries) - found))


def prepare_windows_tools():
    tools = ROOT / "build/windows-tools"
    for item in json.loads((ROOT / "scripts/windows-tools.json").read_text()):
        archive = verified_download(item["url"], item["sha256"],
                                    ROOT / "build/tool-downloads" / (item["name"] + ".zip"))
        extract_tools(archive, tools, item["binaries"], item["name"])
        if item.get("license_url"):
            verified_download(item["license_url"], item["license_sha256"],
                              tools / "licenses" / item["name"] / "LICENSE.md")
    if not any((tools / "licenses/ffmpeg").rglob("*LICENSE*")):
        raise RuntimeError("FFmpeg distribution license is missing")
    for name, flag in (("ffmpeg", "-version"), ("ffprobe", "-version"), ("deno", "--version")):
        result = subprocess.run([str(tools / (name + ".exe")), flag], capture_output=True,
                                text=True, timeout=30, check=True)
        print(result.stdout.splitlines()[0], flush=True)
    with tempfile.TemporaryDirectory() as folder:
        audio = str(Path(folder) / "check.mp3")
        subprocess.run([str(tools / "ffmpeg.exe"), "-v", "error", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=0.2", "-c:a", "libmp3lame", audio],
                       check=True, timeout=30)
        subprocess.run([str(tools / "ffprobe.exe"), "-v", "error", "-show_streams", audio],
                       check=True, timeout=30, capture_output=True)
    return tools


def build_source(output):
    target = Path(output) / ("YT-Duo-" + version() + "-source.zip")
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(filter(None, tracked)):
            if name.startswith("Releases/") and name != "Releases/README.md":
                continue
            path = ROOT / name
            if path.is_file():
                archive.write(path, "YT-Duo/" + name)
    return target.name


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


def build_unix(output):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((ROOT / "src/ytduo").glob("*.py")):
            archive.writestr("ytduo/" + path.name, path.read_bytes())
        for path in sorted((ROOT / "src/ytduo/assets").glob("*.png")):
            archive.writestr("ytduo/assets/" + path.name, path.read_bytes())
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            archive.writestr(name, (ROOT / name).read_bytes())
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    bootstrap = '''import base64, io, os, signal, sys, tempfile, zipfile
if sys.version_info < (3, 10):
    sys.exit("YT-Duo requires Python 3.10 or newer.")
try:
    import tkinter, yt_dlp, yt_dlp_ejs
except ImportError as exc:
    sys.exit("Missing dependency: " + str(exc) + "\\nInstall Tkinter and run: python3 -m pip install -U 'yt-dlp[default]'")
def stop(signum, frame):
    raise SystemExit(128 + signum)
for name in ("SIGTERM", "SIGINT", "SIGHUP"):
    if hasattr(signal, name): signal.signal(getattr(signal, name), stop)
os.environ.setdefault("YTDUO_HOME", os.path.expanduser("~/YT-Duo"))
with tempfile.TemporaryDirectory(prefix="yt-duo-") as folder:
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(PAYLOAD))) as archive:
        archive.extractall(folder)
    sys.path.insert(0, folder)
    from ytduo.cli import main
    raise SystemExit(main(sys.argv[1:] or ["--gui"]))
'''.replace("PAYLOAD", repr(payload))
    script = '''#!/bin/sh
# YT-Duo self-contained source launcher. Python/Tkinter/yt-dlp required.
set -eu
PY="${YTDUO_PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "Install Python 3.10+ or set YTDUO_PYTHON." >&2
    exit 1
fi
exec "$PY" - "$@" <<'YTDUO_SOURCE'
''' + bootstrap + "YTDUO_SOURCE\n"
    for name in ("YT-Duo.sh", "YT-Duo.command"):
        path = output / name
        path.write_text(script, encoding="utf-8", newline="\n")
        path.chmod(0o755)


def build_windows(output):
    if sys.platform != "win32":
        raise RuntimeError("Build the Windows executable on Windows.")
    bundle = ROOT / "build/windows-bundle"
    tools = prepare_windows_tools()
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
        "--add-data", str(ROOT / "LICENSE") + os.pathsep + "licenses/YT-Duo",
        "--add-data", str(ROOT / "THIRD_PARTY_NOTICES.md") + os.pathsep + ".",
        "--collect-all", "yt_dlp_ejs", "--copy-metadata", "yt-dlp-ejs",
        "--paths", str(ROOT / "src"), "--hidden-import", "tkinter.ttk",
        "--exclude-module", "pytest", "--exclude-module", "numpy",
        "--exclude-module", "pandas", "--exclude-module", "matplotlib",
        "--distpath", str(bundle), "--workpath", str(ROOT / "build/pyinstaller"),
        "--specpath", str(ROOT / "build"), str(ROOT / "src/ytduo/_entry.py"),
    ], cwd=ROOT, check=True)
    # Keep standalone tools out of PyInstaller's DLL dependency analysis:
    # otherwise their shared libraries can also be collected at bundle root.
    shutil.copytree(tools, bundle / "YT-Duo/_internal/tools", dirs_exist_ok=True)
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


def checksums(output):
    lines = []
    for name in ("linux/YT-Duo.sh", "macos/YT-Duo.command",
                 "windows/YT-Duo-Portable.exe", "windows/YT-Duo-Setup.exe",
                 "YT-Duo-" + version() + "-source.zip", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        path = output / name
        if path.is_file():
            lines.append(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.relative_to(output).as_posix())
    (output / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows", action="store_true")
    parser.add_argument("--setup", action="store_true", help="Also compile the Windows installer (Inno Setup required).")
    parser.add_argument("--iscc", help="Path to Inno Setup ISCC.exe")
    args = parser.parse_args()
    OUTPUT.mkdir(exist_ok=True)
    staging = ROOT / "build/release-stage"
    artifacts = []
    unix_stage = ROOT / "build/unix"
    unix_stage.mkdir(parents=True, exist_ok=True)
    build_unix(unix_stage)
    for platform, filename in (("linux", "YT-Duo.sh"), ("macos", "YT-Duo.command")):
        destination = staging / platform
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(unix_stage / filename, destination / filename)
        artifacts.append(platform + "/" + filename)
    windows = staging / "windows"
    if args.windows or args.setup:
        windows.mkdir(parents=True, exist_ok=True)
        build_windows(windows)
        artifacts.append("windows/YT-Duo-Portable.exe")
    if args.setup:
        compiler = args.iscc or shutil.which("ISCC.exe")
        if not compiler:
            raise RuntimeError("Install Inno Setup and pass --iscc PATH or add ISCC.exe to PATH.")
        subprocess.run([compiler, "/O" + str(windows),
                        "/DPortableExePath=" + str(windows / "YT-Duo-Portable.exe"),
                        str(ROOT / "scripts/windows_setup.iss")], cwd=ROOT, check=True)
        artifacts.append("windows/YT-Duo-Setup.exe")
    artifacts.append(build_source(staging))
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, staging / name)
        artifacts.append(name)
    for name in artifacts:
        publish_artifact(staging / name, OUTPUT / name)
    checksums(OUTPUT)


if __name__ == "__main__":
    main()
