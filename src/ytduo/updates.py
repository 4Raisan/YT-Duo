"""Stable-release checks and user-initiated, checksum-verified updates."""
import hashlib
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import json
import re
import threading
import time
import urllib.error
import urllib.request
import urllib.parse

REPO_URL = "https://github.com/4Raisan/YT-Duo"
RELEASE_URL = REPO_URL + "/releases/latest"
RELEASE_API = "https://api.github.com/repos/4Raisan/YT-Duo/releases/latest"


def tls_context():
    """Keep system trust and add the packaged public CA roots for sparse stores."""
    import certifi
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


class AssetRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname not in {
                "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}:
            raise ValueError("Untrusted update redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def launcher_path():
    path = os.environ.get("YTDUO_LAUNCHER", "")
    if not path or not Path(path).is_file():
        raise OSError("Run the portable app, installed app, or Unix launcher to update. Source installs: git pull and reinstall dependencies.")
    return Path(path).resolve()


def download_update(latest, progress=None, opener=None):
    """Stage the matching official asset; never touch the running installation."""
    if not version_parts(latest):
        raise ValueError("Invalid update version")
    target = launcher_path()
    # Fail before downloading if the app lives in a read-only folder.
    with tempfile.TemporaryFile(dir=target.parent):
        pass
    platform = sys.platform
    if platform == "win32":
        name, relative = "YT-Duo-" + latest + "-Portable.exe", "windows/"
    elif platform in ("linux", "darwin"):
        name = "YT-Duo-" + latest + ("-macOS.command" if platform == "darwin" else "-Linux.sh")
        relative = "macos/" if platform == "darwin" else "linux/"
    else:
        raise OSError("This platform does not support direct updates.")
    opener = opener or urllib.request.build_opener(
        AssetRedirect(), urllib.request.HTTPSHandler(context=tls_context()))
    base = REPO_URL + "/releases/download/v" + latest + "/"
    def open_asset(filename):
        return opener.open(urllib.request.Request(base + filename,
            headers={"User-Agent": "YT-Duo-updater"}), timeout=30)
    with open_asset("SHA256SUMS.txt") as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError("Update checksum list is too large")
    entries = [line.split() for line in raw.decode("utf-8-sig").splitlines()]
    expected = [parts[0] for parts in entries if len(parts) == 2 and parts[1] in (name, relative + name)]
    if not expected and platform in ("linux", "darwin"):
        # Older releases shipped unversioned launchers only.
        name = "YT-Duo.command" if platform == "darwin" else "YT-Duo.sh"
        expected = [parts[0] for parts in entries if len(parts) == 2 and parts[1] in (name, relative + name)]
    if len(expected) != 1 or not re.fullmatch("[a-fA-F0-9]{64}", expected[0]):
        raise ValueError("Missing or ambiguous update checksum")
    folder = Path(tempfile.mkdtemp(prefix="ytduo-update-"))
    destination = folder / name
    try:
        digest, size = hashlib.sha256(), 0
        with open_asset(name) as response, destination.open("wb") as output:
            while chunk := response.read(256 * 1024):
                size += len(chunk)
                if size > 200 * 1024 * 1024:
                    raise ValueError("Update download exceeds the size limit")
                digest.update(chunk)
                output.write(chunk)
                if progress:
                    progress(size)
        if not size or digest.hexdigest() != expected[0].lower():
            raise ValueError("Update checksum did not match. Your app has not been changed.")
        return str(destination)
    except Exception:
        shutil.rmtree(folder)
        raise


def discard_update(staged):
    """Remove only a known downloaded asset in an app-owned temporary folder."""
    source = Path(staged).absolute()
    folder = source.parent
    if (folder.parent.resolve() != Path(tempfile.gettempdir()).resolve()
            or not folder.name.startswith("ytduo-update-") or folder.is_symlink()
            or source.is_symlink()
            or not re.fullmatch(r"YT-Duo(?:-\d+\.\d+\.\d+-(?:Portable\.exe|Linux\.sh|macOS\.command)|\.sh|\.command)", source.name)):
        return
    try:
        source.unlink(missing_ok=True)
        folder.rmdir()  # Never recursively delete unexpected files.
    except OSError:
        pass


def apply_update(staged):
    """Hand Windows replacement to the new launcher; atomically replace Unix launchers."""
    target, source = launcher_path(), Path(staged).resolve()
    if sys.platform == "win32":
        parent = os.environ.get("YTDUO_LAUNCHER_PID", "0")
        if not parent.isdecimal():
            raise ValueError("Invalid launcher process")
        return subprocess.Popen([str(source), "--apply-update", str(target),
            str(os.getpid()), parent], creationflags=0x08000000, close_fds=True)
    # Keep a rollback copy until the replacement process has been created.
    descriptor, temporary = tempfile.mkstemp(prefix=".ytduo-update-", dir=target.parent)
    backup_fd, backup = tempfile.mkstemp(prefix=".ytduo-backup-", dir=target.parent)
    os.close(backup_fd)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as data:
            shutil.copyfileobj(data, output)
        os.chmod(temporary, target.stat().st_mode)
        shutil.copy2(target, backup)
        os.replace(temporary, target)
        environment = os.environ.copy()
        environment.pop("YTDUO_LAUNCHER", None)
        try:
            child = subprocess.Popen(["/bin/sh", str(target)], env=environment, start_new_session=True)
        except Exception:
            os.replace(backup, target)
            raise
    finally:
        for path in (temporary, backup):
            if os.path.exists(path):
                os.unlink(path)
    try:
        source.unlink()
        source.parent.rmdir()
    except OSError:
        pass  # A successful restart must not be reported as a failed update.
    return child



def version_parts(value):
    if not isinstance(value, str) or not re.fullmatch(r"v?\d+\.\d+\.\d+", value):
        return None
    return tuple(int(n) for n in value.lstrip("v").split("."))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UpdateChecker:
    def __init__(self, version, opener=None, clock=time.monotonic):
        self.version = version
        self.opener = opener or urllib.request.build_opener(
            NoRedirect(), urllib.request.HTTPSHandler(context=tls_context()))
        self.clock = clock
        self._lock = threading.Lock()
        self._cached = None
        self._checked = 0

    def check(self, force=False):
        with self._lock:
            ttl = 60 if self._cached and self._cached["status"] == "unavailable" else 3600
            if not force and self._cached and self.clock() - self._checked < ttl:
                return dict(self._cached)
            result = {"status": "unavailable", "current": self.version}
            request = urllib.request.Request(RELEASE_API, headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "YT-Duo/" + self.version})
            try:
                with self.opener.open(request, timeout=8) as response:
                    raw = response.read(131073)
                    if len(raw) > 131072:
                        raise ValueError("release response too large")
                    release = json.loads(raw)
                candidate = version_parts(release.get("tag_name"))
                current = version_parts(self.version)
                if not candidate or not current or release.get("draft") or release.get("prerelease"):
                    raise ValueError("not a stable release")
                result.update(status="available" if candidate > current else "current",
                              latest=".".join(map(str, candidate)), url=RELEASE_URL)
            except urllib.error.HTTPError as exc:
                result["reason"] = "GitHub returned HTTP " + str(exc.code)
                if exc.code == 404:
                    result["status"] = "unpublished"
                exc.close()
            except OSError:
                result["reason"] = "Could not reach GitHub. Check your connection, proxy or firewall."
            except (ValueError, TypeError, AttributeError):
                result["reason"] = "GitHub returned an unexpected release response."
            if result["status"] == "unavailable":
                # Read only GitHub's redirect; never follow an arbitrary destination.
                try:
                    request = urllib.request.Request(RELEASE_URL, headers={
                        "User-Agent": "YT-Duo/" + self.version})
                    with self.opener.open(request, timeout=8):
                        pass
                except urllib.error.HTTPError as exc:
                    location = urllib.parse.urljoin(RELEASE_URL, exc.headers.get("Location", ""))
                    prefix = REPO_URL + "/releases/tag/"
                    candidate = version_parts(location[len(prefix):]) if location.startswith(prefix) else None
                    current = version_parts(self.version)
                    if exc.code in (301, 302, 303, 307, 308) and candidate and current:
                        result.update(status="available" if candidate > current else "current",
                                      latest=".".join(map(str, candidate)), url=RELEASE_URL)
                        result.pop("reason", None)
                    exc.close()
                except (OSError, ValueError, TypeError, AttributeError):
                    pass
            self._cached, self._checked = result, self.clock()
            return dict(result)
