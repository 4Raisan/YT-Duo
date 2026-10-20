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

#: Environment override for the mode. Named after the project: YTDUO_MODE.
ENV_MODE = "YTDUO_MODE"

#: Legacy name accepted for backward compatibility, with a warning.
LEGACY_ENV_MODE = "DOWNY_MODE"

DEFAULT_SUBDIRS = {
    "output": "YT-Duo Downloads",
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
    """The folder the launcher treats as the working root.

    Resolves in this order:
      1. ``YTDUO_HOME`` -- an explicit override (used by the packaged app).
      2. The frozen bundle's folder, when running as a PyInstaller exe.
      3. The source tree root, whether the package sits at the top level or
         under ``src/`` as it does in this repository.
    """
    override = os.environ.get("YTDUO_HOME")
    if override:
        return os.path.abspath(override)

    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))

    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    # src/ytduo/ -> repository root is one level above src/
    if os.path.basename(parent).lower() == "src":
        return os.path.dirname(parent)
    return parent


def user_downloads_folder():
    """Respect the Windows Downloads known folder, including redirected paths."""
    if sys.platform == "win32":
        import ctypes
        import uuid
        folder_id = ctypes.create_string_buffer(uuid.UUID("374DE290-123F-4565-9164-39C4925E467B").bytes_le)
        output = ctypes.c_wchar_p()
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
        if shell.SHGetKnownFolderPath(folder_id, 0, None, ctypes.byref(output)) == 0:
            try:
                return output.value
            finally:
                ole = ctypes.WinDLL("ole32")
                ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
                ole.CoTaskMemFree(ctypes.cast(output, ctypes.c_void_p))
    return os.path.join(os.path.expanduser("~"), "Downloads")


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
        media_type="video",
        audio_format="original",
        audio_bitrate=None,
    ):
        self.root = os.path.abspath(root or project_root())
        self.mode = mode
        self.link_file = self._abs(link_file)
        self.config_file = self._abs(config_file)
        default_output = DEFAULT_SUBDIRS["output"]
        if not output_dir and root is None and os.environ.get("YTDUO_INSTALLED") == "1":
            default_output = os.path.join(user_downloads_folder(), default_output)
        self.output_dir = self._abs(output_dir or default_output)
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
        if media_type not in ("video", "audio"):
            raise ValueError("media_type must be video or audio")
        if audio_format not in ("original", "mp3"):
            raise ValueError("audio_format must be original or mp3")
        self.media_type = media_type
        self.audio_format = audio_format
        self.audio_bitrate = int(audio_bitrate) if audio_bitrate is not None else None
        if self.audio_bitrate not in (None, 128, 192, 256, 320):
            raise ValueError("audio bitrate must be 128, 192, 256, 320 or best")
        if self.audio_bitrate and audio_format != "mp3":
            raise ValueError("a bitrate choice requires MP3 conversion")

    def _abs(self, path):
        return path if os.path.isabs(path) else os.path.join(self.root, path)

    @property
    def media_output_dir(self):
        return os.path.join(self.output_dir, "Audio") if self.media_type == "audio" else self.output_dir

    @property
    def archive_file(self):
        """yt-dlp's download archive -- what already succeeded.

        Kept inside the output folder so "already downloaded" always means
        "already downloaded *here*". A single shared archive would make a
        changed output directory skip everything silently.
        """
        suffix = "" if self.media_type == "video" else f"-audio-{self.audio_format}"
        if self.media_type == "audio" and self.audio_bitrate:
            suffix += f"-{self.audio_bitrate}k"
        return os.path.join(self.output_dir, f".YT-Duo{suffix}-archive.txt")

    @property
    def work_dirs(self):
        return (self.output_dir, self.media_output_dir, self.video_dir, self.audio_dir)

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
            "media_type": self.media_type,
            "audio_format": self.audio_format,
            "audio_bitrate": self.audio_bitrate,
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
    """Precedence: explicit CLI flag > config.json > YTDUO_MODE > default."""
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
