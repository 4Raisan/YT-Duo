"""Public stable-release notices. Never installs, stars, or sends media data."""
import json
import re
import threading
import time
import urllib.error
import urllib.request

REPO_URL = "https://github.com/4Raisan/YT-Duo"
RELEASE_URL = REPO_URL + "/releases/latest"
RELEASE_API = "https://api.github.com/repos/4Raisan/YT-Duo/releases/latest"


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
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.clock = clock
        self._lock = threading.Lock()
        self._cached = None
        self._checked = 0

    def check(self, force=False):
        with self._lock:
            if not force and self._cached and self.clock() - self._checked < 3600:
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
                if exc.code == 404:
                    result["status"] = "unpublished"
                exc.close()
            except (OSError, ValueError, TypeError, AttributeError):
                pass
            self._cached, self._checked = result, self.clock()
            return dict(result)
