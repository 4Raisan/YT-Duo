"""Settings: every path and tunable in one explicit object.

Replaces the module-level constants that used to live in ``ytd.py``.
Those were lowercase names on a module, which meant importing the module
handed the caller writable handles to its own file layout -- assigning
``ytd.VIDEO_DIR`` silently redirected writes. A Settings instance is
passed explicitly instead, so the layout is a value you can inspect,
replace wholesale in a test, or point at a temp directory.

Paths resolve from ``root`` (default: the project folder), not the current
working directory, so running from anywhere behaves identically.
"""

import json
import os
import sys

DEFAULT_MODE = "best"

#: Environment override for the mode. Named after the project: DOWNY_MODE.
ENV_MODE = "DOWNY_MODE"

#: Legacy name accepted for backward compatibility, with a warning.
LEGACY_ENV_MODE = "DUNY_MODE"

DEFAULT_SUBDIRS = {
    "output": "downloads",
    "video": "video",
    "audio": "audio",
    "recycle": "recycle",
}

#: Recycle bin retention. Intermediates are kept so an interrupted run can
#: resume cheaply, but they are a cache, not an archive: cap both age and
#: total size so the folder cannot grow without bound.
DEFAULT_RECYCLE_MAX_AGE_HOURS = 72
DEFAULT_RECYCLE_MAX_BYTES = 4 * 1024**3


def project_root():
    """The folder containing the ``downy`` package, resolved absolutely."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)


class Settings:
    """Resolved configuration for one run.

    ``root`` may be overridden so tests can run entirely inside a temp
    directory without touching the real download folders.
    """

    def __init__(
        self,
        root=None,
        mode=DEFAULT_MODE,
        link_file="links.txt",
        config_file="config.json",
        output_dir=None,
        video_dir=None,
        audio_dir=None,
        recycle_dir=None,
        socket_timeout=60,
        download_attempts=4,
        retry_delay_seconds=5,
        sleep_interval=0.0,
        recycle_max_age_hours=DEFAULT_RECYCLE_MAX_AGE_HOURS,
        recycle_max_bytes=DEFAULT_RECYCLE_MAX_BYTES,
        use_archive=True,
        ffmpeg_path=None,
        ffprobe_path=None,
        cookies_from_browser=None,
        cookie_file=None,
        require_supported_host=True,
        max_height=None,
    ):
        self.root = os.path.abspath(root or project_root())
        self.mode = mode
        self.link_file = self._abs(link_file)
        self.config_file = self._abs(config_file)
        self.output_dir = self._abs(output_dir or DEFAULT_SUBDIRS["output"])
        self.video_dir = self._abs(video_dir or DEFAULT_SUBDIRS["video"])
        self.audio_dir = self._abs(audio_dir or DEFAULT_SUBDIRS["audio"])
        self.recycle_dir = self._abs(recycle_dir or DEFAULT_SUBDIRS["recycle"])
        self.socket_timeout = socket_timeout
        self.download_attempts = max(1, int(download_attempts))
        self.retry_delay_seconds = max(0, retry_delay_seconds)
        self.sleep_interval = max(0.0, float(sleep_interval))
        self.recycle_max_age_hours = recycle_max_age_hours
        self.recycle_max_bytes = recycle_max_bytes
        self.use_archive = use_archive
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.cookies_from_browser = cookies_from_browser
        self.cookie_file = self._abs(cookie_file) if cookie_file else None
        self.require_supported_host = require_supported_host
        self.max_height = int(max_height) if max_height else None

    def _abs(self, path):
        return path if os.path.isabs(path) else os.path.join(self.root, path)

    @property
    def archive_file(self):
        """yt-dlp's download archive -- what already succeeded.

        Kept inside the output folder so "already downloaded" always means
        "already downloaded *here*". A single shared archive would make a
        changed output directory skip everything silently.
        """
        return os.path.join(self.output_dir, ".downy-archive.txt")

    @property
    def work_dirs(self):
        return (self.output_dir, self.video_dir, self.audio_dir, self.recycle_dir)

    def ensure_dirs(self):
        """Create the working folders. Called by the front-end, not on import."""
        for folder in self.work_dirs:
            os.makedirs(folder, exist_ok=True)

    def replace(self, **changes):
        """Return a copy with some fields changed (used by tests)."""
        fields = {
            "root": self.root,
            "mode": self.mode,
            "link_file": self.link_file,
            "config_file": self.config_file,
            "output_dir": self.output_dir,
            "video_dir": self.video_dir,
            "audio_dir": self.audio_dir,
            "recycle_dir": self.recycle_dir,
            "socket_timeout": self.socket_timeout,
            "download_attempts": self.download_attempts,
            "retry_delay_seconds": self.retry_delay_seconds,
            "sleep_interval": self.sleep_interval,
            "recycle_max_age_hours": self.recycle_max_age_hours,
            "recycle_max_bytes": self.recycle_max_bytes,
            "use_archive": self.use_archive,
            "ffmpeg_path": self.ffmpeg_path,
            "ffprobe_path": self.ffprobe_path,
            "cookies_from_browser": self.cookies_from_browser,
            "cookie_file": self.cookie_file,
            "require_supported_host": self.require_supported_host,
            "max_height": self.max_height,
        }
        fields.update(changes)
        return Settings(**fields)

    def __repr__(self):
        return (
            f"Settings(root={self.root!r}, mode={self.mode!r}, "
            f"output_dir={self.output_dir!r})"
        )


def read_config_file(path):
    """Read config.json, tolerating absence and corruption."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_config(path, data):
    """Persist settings atomically so a crash cannot truncate the file."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def resolve_mode(cli_mode=None, config=None, environ=None):
    """Precedence: explicit CLI flag > config.json > DOWNY_MODE > default."""
    if cli_mode:
        return cli_mode
    config = config or {}
    if config.get("mode"):
        return config["mode"]
    environ = os.environ if environ is None else environ
    env_mode = environ.get(ENV_MODE)
    if env_mode:
        return env_mode
    legacy = environ.get(LEGACY_ENV_MODE)
    if legacy:
        print(
            f"  Warning: {LEGACY_ENV_MODE} is deprecated; use {ENV_MODE}.",
            file=sys.stderr,
        )
        return legacy
    return DEFAULT_MODE
