"""The download engine: probe, fetch, merge, verify.

Deliberately UI-agnostic. It never prints, never prompts, and never reads
``sys.argv``. Progress is reported through an ``emit`` callback that
receives ``(event_name, payload_dict)`` pairs, so a terminal, a GUI, or a
web front-end can all render the same run:

    run_batch(urls, settings, emit=lambda name, data: ...)

Events emitted per item:
    item_start   {index, total, url}
    probe        {index, title, video, audio}
    progress     {index, label, percent, speed, eta}
    stream_done  {index, label, size}
    merged       {index, report}
    item_ok      {index, report}
    item_failed  {index, reason, detail}
    batch_done   {completed, failed, skipped}
"""

import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tempfile
from urllib.parse import urlsplit, parse_qs

import yt_dlp

from . import selector, dependencies
from .naming import build_output_path
from .selector import NoSuitableFormat


# ── Errors ───────────────────────────────────────────────────────────

class MergeError(Exception):
    """ffmpeg failed to produce an output file."""


class ValidationError(Exception):
    """Output was produced but did not match the expected streams."""


class UnsupportedHostError(Exception):
    """yt-dlp has no extractor for this host, and it cannot be guessed at."""


class Cancelled(Exception):
    """The user asked to stop. Raised from progress hooks, not a failure."""


# ── Small helpers ────────────────────────────────────────────────────

_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def strip_ansi(text):
    """Remove any ANSI escape sequence (not just colour: also \\x1b[K, \\x1b[2K)."""
    return _ANSI.sub("", str(text))


def format_size(num_bytes):
    """Human-readable size with B/KB/MB/GB/TB tiers."""
    if not num_bytes or num_bytes <= 0:
        return "? MB"
    size = float(num_bytes)
    for unit in _SIZE_UNITS:
        if size < 1024 or unit == _SIZE_UNITS[-1]:
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def find_ffmpeg(settings=None):
    """Locate ffmpeg: explicit setting, PATH, project folder, common paths."""
    if settings is not None and settings.ffmpeg_path:
        return settings.ffmpeg_path
    bundled = dependencies.bundled_tool("ffmpeg")
    if bundled:
        return bundled
    path = shutil.which("ffmpeg")
    if path:
        return path
    root = settings.root if settings is not None else os.getcwd()
    for candidate in (
        os.path.join(root, "ffmpeg.exe"),
        os.path.expanduser("~/ffmpeg.exe"),
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\ffmpeg\ffmpeg.exe",
    ):
        if os.path.isfile(candidate):
            return candidate
    return None


def find_ffprobe(settings=None):
    """Locate ffprobe next to ffmpeg, or on PATH."""
    if settings is not None and settings.ffprobe_path:
        return settings.ffprobe_path
    bundled = dependencies.bundled_tool("ffprobe")
    if bundled:
        return bundled
    path = shutil.which("ffprobe")
    if path:
        return path
    ffmpeg_path = find_ffmpeg(settings)
    if ffmpeg_path:
        name = "ffprobe.exe" if os.name == "nt" else "ffprobe"
        sibling = os.path.join(os.path.dirname(ffmpeg_path), name)
        if os.path.isfile(sibling):
            return sibling
    return None


PARTIAL_SUFFIXES = (".part", ".ytdl", ".temp")


def find_file(directory, video_id, suffix):
    """Find a completed download, ignoring partials and preferring newest.

    Earlier versions returned ``glob(...)[0]``, which could hand back a
    ``.part`` file or a stale artefact from an interrupted run.
    """
    pattern = os.path.join(directory, f"{video_id}_{suffix}.*")
    matches = [
        f for f in glob.glob(pattern)
        if not f.endswith(PARTIAL_SUFFIXES) and os.path.isfile(f)
    ]
    if not matches:
        return None
    return max(matches, key=os.path.getmtime)


# ── Logging and progress ─────────────────────────────────────────────

class EngineLogger:
    """Route yt-dlp diagnostics into engine events instead of stdout."""

    def __init__(self, emit=None):
        self.emit = emit or (lambda name, data: None)
        self.warnings = []

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        text = strip_ansi(msg).strip()
        if text and not text.startswith("[debug]"):
            self.warnings.append(text)
            self.emit("warning", {"message": text})

    def error(self, msg):
        text = strip_ansi(msg).strip()
        if text:
            self.emit("warning", {"message": text})


def _make_hook(label, index, emit, should_stop=None):
    """Translate yt-dlp's progress dicts into ``progress``/``stream_done`` events.

    ``should_stop`` is polled between chunks so a Cancel click interrupts a
    large in-flight download instead of waiting for it to finish.
    """

    def hook(d):
        if should_stop is not None and should_stop():
            raise Cancelled()

        status = d.get("status")
        if status == "downloading":
            emit("progress", {
                "index": index,
                "label": label,
                "percent": strip_ansi(d.get("_percent_str", "")).strip(),
                "speed": strip_ansi(d.get("_speed_str", "")).strip(),
                "eta": strip_ansi(d.get("_eta_str", "")).strip(),
            })
        elif status == "finished":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            emit("stream_done", {
                "index": index,
                "label": label,
                "size": format_size(total),
            })

    return hook


# ── Error classification ─────────────────────────────────────────────

ERROR_HINTS = [
    ("no extractor for this host", "host not supported by yt-dlp"),
    ("unsupported url", "host not supported by yt-dlp"),
    ("is not a valid url", "not a valid URL"),
    ("incomplete youtube id", "not a valid video URL"),
    ("private video", "private video"),
    ("members-only", "members-only content"),
    ("join this channel", "members-only content"),
    ("sign in to confirm", "YouTube bot check - try --cookies-from-browser"),
    ("confirm you're not a bot", "YouTube bot check - try --cookies-from-browser"),
    ("requested format is not available", "no usable stream for the selected mode"),
    ("video unavailable", "video unavailable"),
    ("http error 429", "rate limited"),
    ("too many requests", "rate limited"),
    ("private or deleted", "private or deleted"),
    ("age-restricted", "age-restricted"),
    ("confirm your age", "age-restricted"),
    ("unable to download webpage", "network or host error"),
    ("timed out", "network timeout"),
    ("database is locked", "archive file is locked by another run"),
    ("no space left", "disk full"),
    ("ffmpeg", "ffmpeg failed"),
]


def classify_error(exc):
    """Map an exception to a short human-readable cause."""
    text = str(exc)
    low = text.lower()
    for needle, label in ERROR_HINTS:
        if needle in low:
            return label
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith("error:"):
            remainder = line.split(":", 1)[1].strip()
            if remainder:
                return remainder
            continue
        return line
    return type(exc).__name__


def is_host_supported(url):
    """True if yt-dlp has an extractor that claims this URL.

    Generic extractors (``GenericIE``) accept almost anything, so they are
    excluded: for a streaming aggregator, "generic" means a guaranteed
    failure several network round-trips later.
    """
    try:
        extractors = yt_dlp.extractor.gen_extractors()
    except Exception:
        return True
    for ie in extractors:
        try:
            if ie.suitable(url) and ie.IE_NAME != "generic":
                return True
        except Exception:
            continue
    return False


# ── yt-dlp option building ───────────────────────────────────────────

def _base_opts(settings, logger):
    opts = {
        "noplaylist": True,
        "continuedl": True,
        "socket_timeout": settings.socket_timeout,
        "retries": settings.download_attempts,
        "fragment_retries": settings.download_attempts,
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "logger": logger,
        "cookiefile": settings.cookie_file,
    }
    if settings.cookies_from_browser:
        opts["cookiesfrombrowser"] = (settings.cookies_from_browser,)
    if settings.use_archive:
        opts["download_archive"] = settings.archive_file
    ffmpeg = find_ffmpeg(settings)
    if ffmpeg:
        opts["ffmpeg_location"] = os.path.dirname(ffmpeg)
    deno = dependencies.find_deno()
    if deno:
        opts["js_runtimes"] = {"deno": {"path": deno}}
    return opts


def probe(url, settings, logger=None):
    """Extract metadata without downloading.

    The download archive is deliberately NOT applied here: with it set and
    the URL already recorded, yt-dlp treats the item as done and returns
    ``None`` instead of metadata, which used to crash the pipeline.
    """
    opts = _base_opts(settings, logger or EngineLogger())
    opts.pop("download_archive", None)
    opts["skip_download"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise yt_dlp.utils.DownloadError(
            f"no metadata returned for {url} (already archived? use --no-archive)")
    return info


RETRYABLE = (
    "403", "forbidden", "timed out", "timeout", "connection reset",
    "bytes read", "more expected", "incomplete read", "connection aborted",
    "remote end closed", "connection reset by peer",
    "503", "service unavailable", "too many requests", "429",
)


def fetch_stream(url, fmt, outtmpl, label, settings, index=0, emit=None,
                 should_stop=None):
    """Download one selected stream. Returns (filepath, info).

    Stream URLs are short-lived and high-bitrate streams are occasionally
    refused with a transient 403, so failures are retried against freshly
    extracted URLs. Resumable partials make this cheap.

    ``should_stop`` lets a front-end interrupt a large in-flight download.
    """
    emit = emit or (lambda name, data: None)
    last_error = None

    for attempt in range(1, settings.download_attempts + 1):
        try:
            if should_stop is not None and should_stop():
                raise Cancelled()
            logger = EngineLogger(emit)
            opts = _base_opts(settings, logger)
            # The archive is keyed per *item*, not per stream: recording it
            # here would mark the video complete before the merge even ran.
            # run_item records it only after a verified success.
            opts.pop("download_archive", None)
            opts["format"] = fmt["format_id"]
            opts["outtmpl"] = outtmpl
            opts["progress_hooks"] = [_make_hook(label, index, emit, should_stop)]
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)

            downloads = info.get("requested_downloads") or []
            path = downloads[0].get("filepath") if downloads else None
            if not path or not os.path.isfile(path):
                suffix = "video" if label == "Video" else "audio"
                directory = settings.video_dir if suffix == "video" else settings.audio_dir
                path = find_file(directory, info["id"], suffix)
            if not path:
                raise FileNotFoundError(
                    f"{label} stream missing on disk for {info['id']}")
            return path, info
        except Cancelled:
            raise
        except yt_dlp.utils.DownloadError as exc:
            # The progress hook raises Cancelled from inside yt-dlp, which
            # may arrive wrapped. Cancellation is never retried.
            if isinstance(getattr(exc, "__cause__", None), Cancelled):
                raise exc.__cause__
            if "cancelled" in str(exc).lower():
                raise Cancelled()
            last_error = exc
            text = str(exc).lower()
            if not any(token in text for token in RETRYABLE):
                raise
            if attempt == settings.download_attempts:
                break
            wait = settings.retry_delay_seconds * attempt
            emit("retry", {
                "index": index,
                "label": label,
                "attempt": attempt,
                "total": settings.download_attempts,
                "wait": wait,
            })
            time.sleep(wait)
    raise last_error


# ── Validation ───────────────────────────────────────────────────────

FFPROBE_VIDEO_CODEC = {"av01": "av1", "vp9": "vp9", "vp8": "vp8", "avc1": "h264"}
FFPROBE_AUDIO_CODEC = {"opus": "opus", "mp4a": "aac"}
FPS_TOLERANCE = 0.5


def _fps_from_ratio(ratio):
    try:
        num, den = str(ratio or "0/1").split("/")
        den = float(den or 1) or 1.0
        return float(num or 0) / den
    except (ValueError, ZeroDivisionError):
        return 0.0


def verify_output(path, video_fmt, audio_fmt, ffprobe_path):
    """ffprobe the merged file and confirm it matches the selected streams."""
    result = subprocess.run(
        [ffprobe_path, "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ValidationError(f"ffprobe could not read {os.path.basename(path)}")

    try:
        probe_data = json.loads(result.stdout)
    except ValueError as exc:
        raise ValidationError(f"ffprobe output not parseable: {exc}")

    streams = probe_data.get("streams", [])
    vs = next((s for s in streams if s.get("codec_type") == "video"), None)
    auds = [s for s in streams if s.get("codec_type") == "audio"]

    if vs is None:
        raise ValidationError("merged file has no video stream")
    if audio_fmt is not None and not auds:
        raise ValidationError("merged file has no audio stream")

    problems = []

    expected_h = selector.height_of(video_fmt)
    if expected_h and vs.get("height") != int(expected_h):
        problems.append(f"height {vs.get('height')} != {int(expected_h)}")

    expected_fps = selector.fps_of(video_fmt)
    actual_fps = _fps_from_ratio(vs.get("avg_frame_rate"))
    if expected_fps and abs(actual_fps - expected_fps) > FPS_TOLERANCE:
        problems.append(f"fps {actual_fps:.2f} != {expected_fps:.0f}")

    family = selector.codec_family(selector.vcodec_of(video_fmt))
    expected_vc = FFPROBE_VIDEO_CODEC.get(family)
    if expected_vc and vs.get("codec_name") != expected_vc:
        problems.append(f"video codec {vs.get('codec_name')} != {expected_vc}")

    if audio_fmt is not None and auds:
        afamily = selector.codec_family(selector.acodec_of(audio_fmt))
        expected_ac = FFPROBE_AUDIO_CODEC.get(afamily)
        if expected_ac and auds[0].get("codec_name") != expected_ac:
            problems.append(f"audio codec {auds[0].get('codec_name')} != {expected_ac}")

    duration = float(probe_data.get("format", {}).get("duration") or 0)
    if duration <= 0:
        problems.append("duration is zero")

    if problems:
        raise ValidationError("; ".join(problems))

    return {
        "resolution": f"{vs.get('width')}x{vs.get('height')}",
        "fps": round(actual_fps, 2),
        "video_codec": vs.get("codec_name"),
        "audio_codec": auds[0].get("codec_name") if auds else None,
        "audio_streams": len(auds),
        "duration": round(duration, 1),
    }


# ── Storage hygiene ──────────────────────────────────────────────────

def recycle(filepath, settings):
    """Move an intermediate into the recycle folder, never clobbering."""
    if not filepath or not os.path.isfile(filepath):
        return None
    os.makedirs(settings.recycle_dir, exist_ok=True)
    base = os.path.basename(filepath)
    dest = os.path.join(settings.recycle_dir, base)
    if os.path.exists(dest):
        name, ext = os.path.splitext(base)
        n = 1
        while os.path.exists(dest):
            dest = os.path.join(settings.recycle_dir, f"{name}_{n}{ext}")
            n += 1
    shutil.move(filepath, dest)
    return dest


def clear_recycle(settings):
    """Remove only the private recycle directory, never linked work paths."""
    target = os.path.abspath(settings.recycle_dir)
    expected = os.path.abspath(os.path.join(settings.state_dir, "work", "recycle"))
    if os.path.normcase(target) != os.path.normcase(expected):
        raise OSError("Refusing to clean a recycle folder outside the application data root")
    for parent in (settings.state_dir, os.path.dirname(target), target):
        if os.path.islink(parent) or getattr(os.path, "isjunction", lambda p: False)(parent):
            raise OSError("Refusing to clean a linked recycle folder")
    if os.path.isdir(target):
        shutil.rmtree(target)


def discard_completed_stream(path, emit):
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError as exc:
            emit("warning", {"message": "Could not remove completed temporary stream: " + str(exc)})


def prune_recycle(settings, emit=None):
    """Enforce the recycle bin's age and size caps. Returns (removed, freed)."""
    emit = emit or (lambda name, data: None)
    if not os.path.isdir(settings.recycle_dir):
        return 0, 0

    now = time.time()
    removed = freed = 0
    if settings.recycle_max_age_hours is not None:
        cutoff = now - settings.recycle_max_age_hours * 3600
        for entry in os.scandir(settings.recycle_dir):
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    size = entry.stat().st_size
                    os.remove(entry.path)
                    removed += 1
                    freed += size
            except OSError:
                continue

    files = []
    for entry in os.scandir(settings.recycle_dir):
        try:
            if entry.is_file():
                files.append((entry.stat().st_mtime, entry.path, entry.stat().st_size))
        except OSError:
            continue
    files.sort()  # oldest first

    total = sum(size for _, _, size in files)
    if settings.recycle_max_bytes is not None:
        for _, path, size in files:
            if total <= settings.recycle_max_bytes:
                break
            try:
                os.remove(path)
                total -= size
                removed += 1
                freed += size
            except OSError:
                continue

    if removed:
        emit("recycle_pruned", {"removed": removed, "freed": format_size(freed)})
    return removed, freed


def sweep_orphans(settings, emit=None):
    """Delete leftover ``.part``/``.ytdl`` files in the work folders and root.

    An interrupted run leaves these behind; they are never useful and can
    be gigabytes. Only the project root and the dedicated work folders are
    scanned -- never a user's whole drive.
    """
    emit = emit or (lambda name, data: None)
    removed = freed = 0
    for folder in (settings.root, settings.video_dir, settings.audio_dir):
        if not os.path.isdir(folder):
            continue
        for entry in os.scandir(folder):
            try:
                if entry.is_file() and entry.name.endswith(PARTIAL_SUFFIXES):
                    size = entry.stat().st_size
                    os.remove(entry.path)
                    removed += 1
                    freed += size
            except OSError:
                continue
    if removed:
        emit("orphans_removed", {"removed": removed, "freed": format_size(freed)})
    return removed, freed


def cleanup_partials(paths):
    """Best-effort removal of intermediates after a failed item."""
    for path in paths:
        if not path:
            continue
        for candidate in (path, path + ".part", path + ".ytdl"):
            try:
                if os.path.isfile(candidate):
                    os.remove(candidate)
            except OSError:
                pass


# ── Merge ────────────────────────────────────────────────────────────

def _container_for_streams(video_fmt, audio_fmt):
    """Pick a container compatible with the streams actually selected.

    The old code used a container hardcoded per mode, which could emit an
    MP4 containing Opus audio -- technically legal, widely unsupported.
    """
    vcodec = selector.codec_family(selector.vcodec_of(video_fmt))
    acodec = selector.codec_family(selector.acodec_of(audio_fmt or video_fmt)) or None

    mp4_video = vcodec in ("avc1", "h264", "av01", "vp9")
    mp4_audio = acodec in (None, "mp4a", "aac")
    if mp4_video and mp4_audio:
        return "mp4"
    # MKV accepts every codec combination we can produce.
    return "mkv"


def merge(index, title, video_file, audio_file, video_fmt, audio_fmt,
          settings, emit=None, extension=None):
    """Mux with stream copy, validate, then recycle the source streams."""
    emit = emit or (lambda name, data: None)

    container = extension or _container_for_streams(video_fmt, audio_fmt)
    out = build_output_path(settings.output_dir, index, title, container,
                            numbered=bool(settings.numbered))

    if os.path.exists(out):
        raise MergeError(f"refusing to overwrite {os.path.basename(out)}")

    inputs = ["-i", video_file]
    if audio_file:
        inputs += ["-i", audio_file]
        maps = ["-map", "0:v:0", "-map", "1:a:0"]
    else:
        maps = ["-map", "0:v:0"]
        if selector.acodec_of(video_fmt):
            maps += ["-map", "0:a:0"]

    result = subprocess.run(
        [find_ffmpeg(settings), "-y", "-loglevel", "error"]
        + inputs + maps + ["-c", "copy", "-movflags", "+faststart", out],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not os.path.isfile(out):
        tail = (result.stderr or "").strip().splitlines()
        if os.path.isfile(out):
            try:
                os.remove(out)
            except OSError:
                pass
        raise MergeError(tail[-1] if tail else "ffmpeg returned no output")

    try:
        report = verify_output(out, video_fmt, audio_file and audio_fmt,
                               find_ffprobe(settings))
    except ValidationError:
        try:
            os.remove(out)
        except OSError:
            pass
        raise

    report["path"] = out

    discard_completed_stream(video_file, emit)
    if audio_file:
        discard_completed_stream(audio_file, emit)

    emit("merged", {"index": index, "report": report})
    return report


# ── Batch orchestration ──────────────────────────────────────────────

def _archive_entries(settings):
    """Map url -> recorded output path (or None for legacy entries)."""
    entries = {}
    if not os.path.exists(settings.archive_file):
        return entries
    try:
        with open(settings.archive_file, encoding="utf-8") as fh:
            for line in fh:
                parts = line.strip().split(" ", 2)
                if len(parts) == 2:
                    entries[parts[1]] = None
                elif len(parts) == 3:
                    entries[parts[1]] = parts[2]
    except OSError:
        return {}
    return entries


def _archive_key(url, settings, selection=None):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    video_id = None
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "m.youtube.com", "music.youtube.com"):
        video_id = parse_qs(parsed.query).get("v", [None])[0]
        if not video_id and parsed.path.startswith(("/shorts/", "/live/", "/embed/")):
            video_id = parsed.path.split("/")[2]
    source = "youtube:" + video_id if video_id else url
    variant = selection if selection is not None else {
        "mode": settings.mode, "max_height": settings.max_height,
        "audio_format": settings.audio_format, "audio_bitrate": settings.audio_bitrate}
    value = {"v": 2, "source": source, "media": settings.media_type, "variant": variant}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def _stream_identity(fmt):
    if fmt is None:
        return None
    result = {name: fmt.get(name) for name in (
        "format_id", "vcodec", "acodec", "width", "height", "fps", "abr", "tbr",
        "asr", "audio_channels", "language", "dynamic_range")}
    for name in ("width", "height", "fps", "abr", "tbr", "asr", "audio_channels"):
        if result[name] is not None:
            try:
                result[name] = float(result[name])
            except (ValueError, TypeError):
                pass
    return result


def _selection_identity(formats, settings):
    if settings.media_type == "audio":
        audio = selector.pick_music(formats, settings.mode)
        return {"audio": _stream_identity(audio), "format": settings.audio_format,
                "bitrate": settings.audio_bitrate if settings.audio_format == "mp3" else None}
    video, audio, _ = selector.pick_streams(formats, settings.mode, settings.max_height)
    return {"video": _stream_identity(video), "audio": _stream_identity(audio),
            "container": _container_for_streams(video, audio)}


def already_downloaded(url, settings, selection=None):
    """True if ``url`` was completed *and its file still exists*.

    The archive stores the output path alongside the URL. An entry whose
    file has since been moved or deleted -- or that was written to a
    different output folder -- is treated as not downloaded, otherwise a
    changed output directory would silently skip everything forever.
    """
    if not settings.use_archive:
        return False
    stored = _archive_entries(settings).get(_archive_key(url, settings, selection))
    return bool(stored and os.path.isfile(stored))


def _record_archive(url, settings, path, selection=None):
    """Append ``url`` and its output path after a verified success.

    Done manually rather than letting yt-dlp record it per stream, so a
    video whose merge failed is retried next run instead of being skipped
    forever. The format is ``YT-Duo <url> <path>``.
    """
    if not settings.use_archive:
        return
    key = _archive_key(url, settings, selection)
    existing = _archive_entries(settings)
    if existing.get(key) == path:
        return
    lines = [
        f"YT-Duo {u}" + (f" {p}" if p else "")
        for u, p in existing.items() if u != key
    ]
    lines.append(f"YT-Duo {key} {path}")
    stage = None
    try:
        os.makedirs(os.path.dirname(settings.archive_file), exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False,
                                         dir=os.path.dirname(settings.archive_file)) as fh:
            stage = fh.name
            fh.write("\n".join(lines) + "\n")
        os.replace(stage, settings.archive_file)
    except OSError:
        pass
    finally:
        if stage and os.path.exists(stage):
            try:
                os.remove(stage)
            except OSError:
                pass


def verify_audio_output(path, fmt, settings):
    """Validate an audio-only output before recording it as completed."""
    result = subprocess.run(
        [find_ffprobe(settings), "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", path], capture_output=True, text=True)
    if result.returncode:
        raise ValidationError("ffprobe could not read the audio output")
    try:
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        duration = float(data.get("format", {}).get("duration") or 0)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("invalid audio verification data") from exc
    if not audio or any(s.get("codec_type") == "video" for s in streams) or duration <= 0:
        raise ValidationError("output must contain audio, no video, and positive duration")
    family = selector.codec_family(selector.acodec_of(fmt))
    expected = "mp3" if settings.audio_format == "mp3" else FFPROBE_AUDIO_CODEC.get(family, family)
    if expected and audio.get("codec_name") != expected:
        raise ValidationError(f"audio codec {audio.get('codec_name')} != {expected}")
    bitrate = int(audio.get("bit_rate") or 0)
    if settings.audio_format == "mp3" and settings.audio_bitrate:
        expected_bitrate = settings.audio_bitrate * 1000
        if abs(bitrate - expected_bitrate) > expected_bitrate * 0.02:
            raise ValidationError("MP3 output bitrate does not match the selected quality")
    return {"resolution": "Audio", "fps": 0, "video_codec": None,
            "audio_codec": audio.get("codec_name"), "audio_streams": 1,
            "duration": round(duration, 1), "media_type": "audio", "path": path,
            "audio_bitrate": bitrate}


def run_music(url, index, title, formats, settings, emit, should_stop=None):
    fmt = selector.pick_music(formats, settings.mode)
    emit("probe", {"index": index, "title": title, "video": "Music / audio only",
                   "audio": selector.describe(fmt, True)})
    source, _ = fetch_stream(url, fmt, os.path.join(settings.audio_dir,
                             "%(id)s_audio.%(ext)s"), "Audio", settings,
                             index, emit, should_stop)
    family = selector.codec_family(selector.acodec_of(fmt))
    extension = "mp3" if settings.audio_format == "mp3" else {
        "mp4a": "m4a", "opus": "opus", "vorbis": "ogg", "mp3": "mp3",
        "flac": "flac"}.get(family, "mka")
    os.makedirs(settings.media_output_dir, exist_ok=True)
    output = build_output_path(settings.media_output_dir, index, title, extension,
                               numbered=bool(settings.numbered))
    # Reserve our own output atomically; cleanup must never delete another run's file.
    with open(output, "xb"):
        pass
    if settings.audio_format == "mp3":
        quality = ["-b:a", f"{settings.audio_bitrate}k"] if settings.audio_bitrate else ["-q:a", "0"]
        encoding = ["-c:a", "libmp3lame"] + quality
    else:
        encoding = ["-c:a", "copy"]
    try:
        if should_stop and should_stop():
            raise Cancelled()
        result = subprocess.run([find_ffmpeg(settings), "-y", "-loglevel", "error",
            "-i", source, "-map", "0:a:0", "-vn"] + encoding + [output],
            capture_output=True, text=True)
        if result.returncode or not os.path.isfile(output):
            raise MergeError((result.stderr or "audio extraction failed").strip()[-300:])
        report = verify_audio_output(output, fmt, settings)
    except Exception:
        if os.path.isfile(output):
            os.remove(output)
        raise
    discard_completed_stream(source, emit)
    emit("merged", {"index": index, "report": report})
    return report


def run_item(url, index, total, settings, emit=None, should_stop=None,
             confirm_redownload=None):
    """Process one URL end-to-end. Raises on failure; returns a report.

    ``should_stop`` is polled between chunks of an in-flight download, so
    Cancel interrupts immediately rather than at the next queue item.
    """
    emit = emit or (lambda name, data: None)
    if settings.numbered is None:
        settings = settings.replace(numbered=total > 1)
    emit("item_start", {"index": index, "total": total, "url": url})

    # Fail fast on hosts yt-dlp has no extractor for. Previously these cost
    # a metadata round-trip and then failed with a vague message.
    if settings.require_supported_host and not is_host_supported(url):
        raise UnsupportedHostError(
            f"no extractor for this host: {url}")

    logger = EngineLogger(emit)
    info = probe(url, settings, logger)
    title = info.get("title") or info.get("id") or f"item {index}"
    formats = info.get("formats") or []
    if should_stop and should_stop():
        raise Cancelled()
    selection = _selection_identity(formats, settings)
    if already_downloaded(url, settings, selection):
        approved = bool(confirm_redownload(url)) if confirm_redownload else False
        if should_stop and should_stop():
            raise Cancelled()
        if not approved:
            emit("item_skipped", {"index": index, "url": url})
            return {"skipped": True}
    if settings.media_type == "audio":
        report = run_music(url, index, title, formats, settings, emit, should_stop)
        _record_archive(url, settings, report["path"], selection)
        return report
    video_fmt, audio_fmt, needs_merge = selector.pick_streams(
        formats, settings.mode, settings.max_height)

    emit("probe", {
        "index": index,
        "title": title,
        "video": selector.describe(video_fmt),
        "audio": selector.describe(audio_fmt, True) if audio_fmt else None,
    })

    video_path = audio_path = None
    try:
        video_path, _ = fetch_stream(
            url, video_fmt, os.path.join(settings.video_dir, "%(id)s_video.%(ext)s"),
            "Video", settings, index, emit, should_stop)
        if audio_fmt:
            audio_path, _ = fetch_stream(
                url, audio_fmt, os.path.join(settings.audio_dir, "%(id)s_audio.%(ext)s"),
                "Audio", settings, index, emit, should_stop)

        report = merge(index, title, video_path, audio_path, video_fmt,
                       audio_fmt, settings, emit)
        _record_archive(url, settings, report["path"], selection)
        return report
    except (NoSuitableFormat, MergeError, ValidationError, KeyboardInterrupt,
            Cancelled):
        # Keep intermediates in recycle/ -- a rerun can often resume them.
        raise
    except Exception:
        # An unrelated crash means the intermediates are suspect: drop them.
        cleanup_partials([video_path, audio_path])
        raise


def run_batch(urls, settings, emit=None, should_stop=None, take_more=None,
              confirm_redownload=None):
    """Download every URL in order. Returns a summary dict.

    ``should_stop`` is an optional callable polled between items *and*
    between chunks of each download, so a GUI can cancel promptly.

    ``take_more`` makes the run a *live* queue: after each item it is called
    and may return further URLs, which are downloaded before the batch ends.
    That is what lets someone paste another link while a download is already
    running instead of waiting for it to finish.
    """
    emit = emit or (lambda name, data: None)
    should_stop = should_stop or (lambda: False)

    settings.ensure_dirs()
    try:
        clear_recycle(settings)
    except OSError as exc:
        emit("warning", {"message": "Recycle cleanup: " + str(exc)})
    results = []
    queue = list(urls)
    total = len(queue)
    cancelled = False
    interrupted_index = None
    position = 0
    queue_mode = len(queue) > 1

    while queue:
        if should_stop():
            cancelled = True
            break

        url = queue.pop(0)
        position += 1

        item_settings = settings.replace(numbered=queue_mode if settings.numbered is None else settings.numbered)
        try:
            report = run_item(url, position, total, item_settings, emit,
                              should_stop, confirm_redownload=confirm_redownload)
            if report.get("skipped") is True:
                results.append({"url": url, "ok": True, "skipped": True})
            else:
                results.append({"url": url, "ok": True, "report": report})
                emit("item_ok", {"index": position, "report": report})
        except (KeyboardInterrupt, Cancelled):
            emit("batch_interrupted", {"index": position})
            cancelled = True
            interrupted_index = position
            break
        except Exception as exc:
            detail = str(exc)
            results.append({"url": url, "ok": False,
                            "reason": classify_error(exc),
                            "detail": detail})
            emit("item_failed", {
                "index": position,
                "reason": classify_error(exc),
                "detail": detail,
            })
        # Anything queued while we were busy joins the run.
        if take_more is not None and not should_stop():
            try:
                extra = list(take_more() or [])
            except Exception:  # noqa: BLE001 - a bad callback must not stop us
                extra = []
            if extra:
                queue_mode = True
                queue.extend(extra)
                total += len(extra)
                emit("queue_grew", {"added": len(extra), "total": total,
                                    "position": position})

        if queue and settings.sleep_interval:
            time.sleep(settings.sleep_interval)

    completed = sum(1 for r in results if r["ok"] and not r.get("skipped"))
    failed = sum(1 for r in results if not r["ok"])
    # An item interrupted mid-flight started but produced nothing, which is
    # different from one that was never reached.
    abandoned = 1 if interrupted_index is not None else 0
    summary = {
        "total": total,
        "completed": completed,
        "failed": failed,
        "skipped": sum(1 for r in results if r.get("skipped")),
        "cancelled": cancelled,
        "abandoned": abandoned,
        "pending": max(0, total - len(results) - abandoned),
        "results": results,
    }
    emit("batch_done", summary)
    return summary
