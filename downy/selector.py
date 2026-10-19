"""Adaptive format selection for yt-dlp streams.

Pure functions over yt-dlp format metadata dicts. No network, no I/O, no
hardcoded format IDs.

Each mode declares an explicit sort key, so the ranking policy is data,
not control flow. Keys are applied in the listed order:

    height       vertical resolution
    fps          frame rate
    vbr          video bitrate
    abr          audio bitrate
    codec_rank   preference from the mode's codec table
    protocol     direct progressive outranks fragmented
    format_id    deterministic final tiebreak

Stream shapes:
    video-only : has vcodec, no acodec
    audio-only : has acodec, no vcodec
    muxed      : has both -- used only when split streams are unavailable
"""

MODES = {
    "best": {
        "video_sort": ("height", "fps", "vbr", "codec_rank", "protocol", "format_id"),
        "audio_sort": ("codec_rank", "abr", "protocol", "format_id"),
        "video_codec_rank": {"av01": 3, "vp9": 2, "vp8": 1, "avc1": 1, "h264": 1},
        "audio_codec_rank": {"opus": 3, "mp4a": 2, "aac": 2},
        "container": "mkv",
        "protocols": ("https",),
        "allow_hls": False,
        "allow_hdr": False,
    },
    "efficient": {
        "video_sort": ("height", "fps", "codec_rank", "vbr", "protocol", "format_id"),
        "audio_sort": ("codec_rank", "abr", "protocol", "format_id"),
        "video_codec_rank": {"av01": 3, "vp9": 2, "vp8": 1, "avc1": 1, "h264": 1},
        "audio_codec_rank": {"opus": 3, "mp4a": 2, "aac": 2},
        "container": "mkv",
        "protocols": ("https",),
        "allow_hls": False,
        "allow_hdr": False,
    },
    "compat": {
        "video_sort": ("codec_rank", "height", "fps", "vbr", "protocol", "format_id"),
        "audio_sort": ("codec_rank", "abr", "protocol", "format_id"),
        "video_codec_rank": {"avc1": 3, "h264": 3, "vp9": 2, "av01": 1},
        "audio_codec_rank": {"mp4a": 3, "aac": 3, "opus": 2},
        "container": "mp4",
        "protocols": ("https",),
        "allow_hls": False,
        "allow_hdr": False,
    },
}

HDR_RANGES = {"HDR10", "HDR10+", "DV", "DOLBY_VISION", "HDR"}
SDR_RANGES = {"", "sdr", "none"}
STORYBOARD = "images"
DIRECT_PROTOCOL = "https"
FRAGMENTED_PROTOCOL = "m3u8_native"


class NoSuitableFormat(Exception):
    """Raised when no usable stream of the requested kind exists."""


def _text(value):
    return (value or "").lower()


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def vcodec_of(fmt):
    codec = _text(fmt.get("vcodec"))
    return None if codec in ("", "none", STORYBOARD) else codec


def acodec_of(fmt):
    codec = _text(fmt.get("acodec"))
    return None if codec in ("", "none") else codec


def codec_family(codec):
    """Collapse a full codec string to its family name."""
    if not codec:
        return ""
    if codec.startswith("av01"):
        return "av01"
    if codec.startswith(("vp09", "vp9")):
        return "vp9"
    if codec.startswith(("avc1", "avc3", "h264")):
        return "avc1"
    if codec.startswith(("mp4a", "aac")):
        return "mp4a"
    return codec


def is_video_only(fmt):
    return vcodec_of(fmt) is not None and acodec_of(fmt) is None


def is_audio_only(fmt):
    return acodec_of(fmt) is not None and vcodec_of(fmt) is None


def is_muxed(fmt):
    return vcodec_of(fmt) is not None and acodec_of(fmt) is not None


def is_storyboard(fmt):
    return _text(fmt.get("vcodec")) == STORYBOARD


def is_drc(fmt):
    """DRC variants compress dynamics at equal bitrate -- not an upgrade."""
    marker = _text(fmt.get("format_id")) + " " + _text(fmt.get("format_note"))
    return "drc" in marker


def is_drm(fmt):
    return bool(fmt.get("has_drm"))


def dynamic_range_of(fmt):
    return _text(fmt.get("dynamic_range"))


def height_of(fmt):
    if fmt.get("height"):
        return _number(fmt.get("height"))
    resolution = _text(fmt.get("resolution"))
    if "x" in resolution:
        try:
            return float(resolution.split("x")[1])
        except (IndexError, ValueError):
            return 0.0
    return 0.0


def fps_of(fmt):
    return _number(fmt.get("fps"))


def bitrate_of(fmt, audio=False):
    key = "abr" if audio else "vbr"
    value = fmt.get(key)
    if value is None:
        value = fmt.get("tbr")
    return _number(value)


def protocol_rank(fmt, cfg):
    protocol = _text(fmt.get("protocol"))
    if protocol == DIRECT_PROTOCOL:
        return 2
    if protocol in cfg["protocols"]:
        return 1
    return 0


def _sort_key(fmt, cfg, order, audio=False):
    key = []
    for name in order:
        if name == "height":
            key.append(height_of(fmt))
        elif name == "fps":
            key.append(fps_of(fmt))
        elif name == "vbr":
            key.append(bitrate_of(fmt, audio=False))
        elif name == "abr":
            key.append(bitrate_of(fmt, audio=True))
        elif name == "codec_rank":
            table = cfg["audio_codec_rank"] if audio else cfg["video_codec_rank"]
            codec = acodec_of(fmt) if audio else vcodec_of(fmt)
            key.append(table.get(codec_family(codec), 0))
        elif name == "protocol":
            key.append(protocol_rank(fmt, cfg))
        elif name == "format_id":
            key.append(_text(fmt.get("format_id")))
    return tuple(key)


def _gate(formats, cfg, audio=False):
    pool = [
        fmt for fmt in formats
        if not is_storyboard(fmt)
        and not is_drm(fmt)
        and protocol_rank(fmt, cfg) > 0
    ]
    if not cfg["allow_hdr"]:
        sdr = [fmt for fmt in pool if dynamic_range_of(fmt) in SDR_RANGES]
        if sdr:
            pool = sdr
    if audio:
        pool = [
            fmt for fmt in pool
            if is_audio_only(fmt)
            and not is_drc(fmt)
            and bitrate_of(fmt, audio=True) > 0
        ]
    else:
        pool = [fmt for fmt in pool if vcodec_of(fmt) is not None]
    return pool


def pick_video(formats, mode="best", max_height=None):
    """Return the best video stream, falling back to a muxed stream.

    ``max_height`` caps the resolution without forcing an exact tier: asking
    for 720p on a video that only offers 480p yields 480p, not a failure.
    Candidates above the cap are dropped; if that empties the pool the cap is
    ignored rather than raising, because a stream at the wrong size is still
    better than no download.
    """
    cfg = _config(mode)
    pool = _gate(formats, cfg, audio=False)
    split = [fmt for fmt in pool if is_video_only(fmt)]
    candidates = split or pool
    if max_height:
        capped = [fmt for fmt in candidates if height_of(fmt) <= max_height]
        if capped:
            candidates = capped
    if not candidates:
        raise NoSuitableFormat("no video stream available")
    return max(
        candidates,
        key=lambda fmt: _sort_key(fmt, cfg, cfg["video_sort"]),
    )


def pick_audio(formats, mode="best"):
    """Return the best audio-only stream."""
    cfg = _config(mode)
    pool = _gate(formats, cfg, audio=True)
    if not pool:
        raise NoSuitableFormat("no audio-only stream available")
    return max(
        pool,
        key=lambda fmt: _sort_key(fmt, cfg, cfg["audio_sort"], audio=True),
    )


def pick_streams(formats, mode="best", max_height=None):
    """Return (video, audio, needs_merge)."""
    video = pick_video(formats, mode, max_height)
    if not is_video_only(video):
        return video, None, False
    try:
        return video, pick_audio(formats, mode), True
    except NoSuitableFormat:
        return video, None, False


def available_heights(formats, mode="best"):
    """Distinct resolutions offered, largest first -- for a GUI picker."""
    cfg = _config(mode)
    pool = _gate(formats, cfg, audio=False)
    return sorted({int(height_of(f)) for f in pool if height_of(f)}, reverse=True)


def container_for(mode="best"):
    return _config(mode)["container"]


def describe(fmt, audio=False):
    """Human-readable one-line summary for progress output."""
    if fmt is None:
        return "none"
    codec = acodec_of(fmt) if audio else vcodec_of(fmt)
    if audio:
        return f"{fmt.get('format_id')} {codec_family(codec)} {bitrate_of(fmt, audio=True):.0f}k"
    return (
        f"{fmt.get('format_id')} {codec_family(codec)} "
        f"{fmt.get('resolution')} @ {fps_of(fmt):.0f}fps "
        f"{bitrate_of(fmt):.0f}k"
    )


def _config(mode):
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; choose from {sorted(MODES)}")
    return MODES[mode]
