"""Selective downloads for indexed MP4/WebM replays, resolved by yt-dlp.

All network reads are bounded. Unsupported indexes fail closed; this module
never invokes yt-dlp's full-file downloader or FFmpeg's network protocols.
"""

import math
import json
import os
import re
import struct
import subprocess
import tempfile
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from yt_dlp.networking import Request


class SplitError(ValueError):
    """A split cannot be downloaded safely with the selected streams."""


def timestamp(value):
    """Accept seconds, MM:SS or HH:MM:SS; reject negatives/NaN/infinity."""
    try:
        parts = str(value).strip().split(":")
        if not 1 <= len(parts) <= 3 or any(not re.fullmatch(r"\d+(?:\.\d+)?", p) for p in parts):
            raise ValueError
        numbers = [float(p) for p in parts]
        if len(parts) > 1 and any(n >= 60 for n in numbers[1:]):
            raise ValueError
        result = sum(n * 60 ** i for i, n in enumerate(reversed(numbers)))
        if not math.isfinite(result):
            raise ValueError
        return result
    except (ValueError, TypeError, OverflowError):
        raise SplitError("Use seconds, MM:SS or HH:MM:SS for split times.") from None


def validate(start, end, info=None):
    if start is None and end is None:
        return None
    if start is None or end is None:
        raise SplitError("Set both split start and end times.")
    start, end = timestamp(start), timestamp(end)
    if end <= start:
        raise SplitError("Split end must be after start.")
    if info is not None:
        if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
            raise SplitError("Split supports finished, indexed videos and replays; wait for this live stream to finish processing.")
        duration = float(info.get("duration") or 0)
        if not math.isfinite(duration) or duration <= 0 or end > duration:
            raise SplitError("Split end must be within the video's known duration.")
    return start, end


def suffix(start, end):
    def clock(value):
        milliseconds = round(value * 1000)
        seconds, ms = divmod(milliseconds, 1000)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        return f"{hours:02d}-{minutes:02d}-{seconds:02d}" + (f".{ms:03d}" if ms else "")
    return f" [{clock(start)}_{clock(end)}]"


class RangeReader:
    """Use yt-dlp's HTTP session (cookies/proxy/TLS), never unbounded GETs."""

    MAX_READ = 1024 * 1024

    def __init__(self, ydl, fmt, settings, check, progress=None):
        self.ydl, self.fmt, self.settings = ydl, fmt, settings
        self.check, self.progress = check, progress or (lambda n: None)
        self.size = int(fmt.get("filesize") or 0)
        if self.size <= 0 or fmt.get("protocol", "https") not in ("http", "https"):
            raise SplitError("Selected stream has no bounded MP4/WebM file index. No full download was started.")
        self.received = 0

    def read(self, start, end):
        if not 0 <= start <= end < self.size or end - start >= self.MAX_READ:
            raise SplitError("Invalid or oversized media range.")
        url = urlsplit(self.fmt["url"])
        headers = dict(self.fmt.get("http_headers") or {})
        headers = {k: v for k, v in headers.items() if k.lower() not in ("range", "accept-encoding")}
        headers["Accept-Encoding"] = "identity"
        google = (url.hostname or "").endswith(".googlevideo.com")
        if google:
            query = [(k, v) for k, v in parse_qsl(url.query, keep_blank_values=True) if k != "range"]
            query.append(("range", f"{start}-{end}"))
            address = urlunsplit(url._replace(query=urlencode(query)))
        else:
            address = self.fmt["url"]
            headers["Range"] = f"bytes={start}-{end}"
        expected = end - start + 1
        for attempt in range(self.settings.download_attempts):
            self.check()
            try:
                with self.ydl.urlopen(Request(address, headers=headers)) as response:
                    if response.headers.get("Content-Encoding", "identity") != "identity":
                        raise SplitError("Server compressed a byte range; download stopped.")
                    length = response.headers.get("Content-Length")
                    if length is not None and int(length) != expected:
                        raise SplitError("Server ignored the byte range; full download refused.")
                    content_range = response.headers.get("Content-Range")
                    if content_range is not None:
                        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
                        if not match or tuple(map(int, match.groups())) != (start, end, self.size):
                            raise SplitError("Server returned a different media range.")
                    elif not google:
                        raise SplitError("Server did not confirm the requested range.")
                    if not google and response.status != 206:
                        raise SplitError("Server ignored partial transfer status.")
                    data = bytearray()
                    while len(data) <= expected:
                        self.check()
                        chunk = response.read(min(65536, expected + 1 - len(data)))
                        if not chunk:
                            break
                        self.received += len(chunk)
                        self.progress(len(chunk))
                        data.extend(chunk)
                    if len(data) > expected:
                        raise SplitError("Server exceeded the requested range; transfer aborted.")
                    if len(data) != expected:
                        raise OSError("Incomplete byte range")
                    return bytes(data)
            except SplitError:
                raise
            except Exception:
                self.check()  # Cancellation is never retried.
                if attempt + 1 == self.settings.download_attempts:
                    raise
                deadline = time.monotonic() + min(5, self.settings.retry_delay_seconds)
                while time.monotonic() < deadline:
                    self.check()
                    time.sleep(0.05)

    def block(self, start, size):
        if not 0 < size <= 8 * 1024 * 1024:
            raise SplitError("Unsupported large or empty index.")
        return b"".join(self.read(p, min(p + self.MAX_READ - 1, start + size - 1))
                        for p in range(start, start + size, self.MAX_READ))


def mp4_index(reader):
    init, offset = [], 0
    for _ in range(32):
        header = reader.block(offset, 16)
        size, kind = struct.unpack_from(">I4s", header)
        h = 8
        if size == 1:
            size, h = struct.unpack_from(">Q", header, 8)[0], 16
        if size < h or offset + size > reader.size:
            raise SplitError("Invalid MP4 box size.")
        if kind in (b"ftyp", b"moov"):
            init.append(reader.block(offset, size))
        if kind == b"sidx":
            index = reader.block(offset, size)
            version = index[h]
            if version not in (0, 1):
                raise SplitError("Unsupported SIDX version.")
            reader.mp4_track_id = struct.unpack_from(">I", index, h + 4)[0]
            pos = h + 8
            scale = struct.unpack_from(">I", index, pos)[0]
            pos += 4
            layout = ">QQ" if version else ">II"
            earliest, first = struct.unpack_from(layout, index, pos)
            pos += 16 if version else 8
            count = struct.unpack_from(">H", index, pos + 2)[0]
            pos += 4
            if not scale or not count or pos + count * 12 != len(index) or len(init) < 2:
                raise SplitError("Incomplete MP4 index.")
            byte, clock, rows = offset + size + first, earliest / scale, []
            for _ in range(count):
                reference, duration, _sap = struct.unpack_from(">III", index, pos)
                pos += 12
                if reference >> 31 or not reference or not duration:
                    raise SplitError("Nested or invalid MP4 indexes are not supported.")
                stop = clock + duration / scale
                rows.append((clock, stop, byte, byte + reference - 1))
                clock, byte = stop, byte + reference
            return b"".join(init), rows
        if kind == b"mdat":
            break
        offset += size
    raise SplitError("Selected MP4 has no supported SIDX index. Full transfer refused.")


def mp4_boxes(data):
    """Yield bounded MP4 box payloads; used for timing metadata, never mdat."""
    pos = 0
    while pos < len(data):
        if pos + 8 > len(data):
            raise SplitError("Truncated MP4 timing box.")
        size, kind = struct.unpack_from(">I4s", data, pos)
        header = 8
        if size == 1:
            if pos + 16 > len(data):
                raise SplitError("Truncated extended MP4 timing box.")
            size, header = struct.unpack_from(">Q", data, pos + 8)[0], 16
        if size < header or pos + size > len(data):
            raise SplitError("Invalid MP4 timing box size.")
        yield kind, data[pos + header:pos + size]
        pos += size


def mp4_presentation_origin(reader, init, rows):
    """Read only the first moof metadata to establish the presentation origin.

    FFmpeg 6 can write a SIDX origin at decode time zero while the first video
    frame presents later because of B-frame reordering. Newer writers include
    that offset in SIDX. The first trun composition offset disambiguates them;
    no first-fragment media payload is downloaded for this check.
    """
    moov = dict(mp4_boxes(init))[b"moov"]
    movie = dict(mp4_boxes(moov))
    mvhd = movie[b"mvhd"]
    movie_scale = struct.unpack_from(">I", mvhd, 20 if mvhd[0] else 12)[0]
    scale, shift = None, 0
    for kind, payload in mp4_boxes(moov):
        if kind != b"trak":
            continue
        track = dict(mp4_boxes(payload))
        tkhd = track[b"tkhd"]
        track_id = struct.unpack_from(">I", tkhd, 20 if tkhd[0] else 12)[0]
        if track_id != reader.mp4_track_id:
            continue
        mdhd = dict(mp4_boxes(track[b"mdia"]))[b"mdhd"]
        scale = struct.unpack_from(">I", mdhd, 20 if mdhd[0] else 12)[0]
        if not scale or not movie_scale:
            raise SplitError("Invalid MP4 presentation timescale.")
        if b"edts" in track:
            elst = dict(mp4_boxes(track[b"edts"]))[b"elst"]
            if elst[0] not in (0, 1):
                raise SplitError("Unsupported source edit-list version.")
            count, pos = struct.unpack_from(">I", elst, 4)[0], 8
            if not 1 <= count <= 2:
                raise SplitError("Complex source edit lists are unsupported.")
            for _ in range(count):
                layout = ">Qqhh" if elst[0] else ">Iihh"
                length, media_time, integer, fraction = struct.unpack_from(layout, elst, pos)
                pos += struct.calcsize(layout)
                if (integer, fraction) != (1, 0):
                    raise SplitError("Nonstandard source playback rate.")
                if media_time == -1:
                    shift += length / movie_scale
                else:
                    shift -= media_time / scale
                    break
        break
    if scale is None:
        raise SplitError("Source video track timing was not found.")
    offset = rows[0][2]
    for _ in range(8):
        header = reader.block(offset, 16)
        size, kind = struct.unpack_from(">I4s", header)
        h = 8
        if size == 1:
            size, h = struct.unpack_from(">Q", header, 8)[0], 16
        if size < h or offset + size - 1 > rows[0][3]:
            raise SplitError("Invalid first-fragment timing metadata.")
        if kind == b"mdat":
            break
        if kind == b"moof":
            moof = reader.block(offset, size)[h:]
            for child, payload in mp4_boxes(moof):
                if child != b"traf":
                    continue
                children = list(mp4_boxes(payload))
                if sum(k == b"trun" for k, _ in children) != 1:
                    raise SplitError("Multiple runs in the first fragment are unsupported.")
                fields = dict(children)
                if struct.unpack_from(">I", fields[b"tfhd"], 4)[0] != reader.mp4_track_id:
                    continue
                tfdt, trun = fields[b"tfdt"], fields[b"trun"]
                if tfdt[0] not in (0, 1) or trun[0] not in (0, 1):
                    raise SplitError("Unsupported fragment timing version.")
                decode_time = struct.unpack_from(">Q" if tfdt[0] else ">I", tfdt, 4)[0]
                flags = int.from_bytes(trun[1:4], "big")
                if not struct.unpack_from(">I", trun, 4)[0]:
                    raise SplitError("Empty first video fragment.")
                pos = 8 + (4 if flags & 1 else 0) + (4 if flags & 4 else 0)
                pos += 4 * sum(bool(flags & mask) for mask in (0x100, 0x200, 0x400))
                composition = struct.unpack_from(">i" if trun[0] else ">I", trun, pos)[0] if flags & 0x800 else 0
                return (decode_time + composition) / scale + shift
            break
        offset += size
    raise SplitError("First video fragment has no supported presentation timing.")


def vint(data, pos=0, identity=False):
    if pos >= len(data) or not data[pos]:
        raise SplitError("Invalid WebM integer.")
    length = 1
    while not data[pos] & (1 << (8 - length)):
        length += 1
    if pos + length > len(data):
        raise SplitError("Truncated WebM integer.")
    value = int.from_bytes(data[pos:pos + length], "big")
    return (value if identity else value & ((1 << (7 * length)) - 1)), length


def elements(data):
    pos = 0
    while pos < len(data):
        ident, a = vint(data, pos, True)
        size, b = vint(data, pos + a)
        start = pos + a + b
        if start + size > len(data):
            raise SplitError("Truncated WebM index.")
        yield ident, data[start:start + size]
        pos = start + size


def webm_index(reader, duration):
    def header(at):
        data = reader.block(at, 16)
        ident, a = vint(data, identity=True)
        size, b = vint(data, a)
        return ident, size, a + b
    ident, size, h = header(0)
    if ident != 0x1A45DFA3:
        raise SplitError("Missing WebM header.")
    prefix = reader.block(0, size + h)
    offset = size + h
    ident, size, h = header(offset)
    if ident != 0x18538067:
        raise SplitError("Missing WebM segment.")
    base = offset + h
    offset, init, cuespos, scale = base, [], None, .001
    for _ in range(32):
        ident, size, h = header(offset)
        if ident == 0x1F43B675:
            break
        box = reader.block(offset, size + h)
        if ident in (0x1549A966, 0x1654AE6B):
            init.append(box)
        if ident == 0x1549A966:
            fields = dict(elements(box[h:]))
            scale = int.from_bytes(fields.get(0x2AD7B1, b"\x0f\x42\x40"), "big") / 1e9
        if ident == 0x114D9B74:
            for sid, seek in elements(box[h:]):
                if sid == 0x4DBB:
                    fields = dict(elements(seek))
                    if int.from_bytes(fields.get(0x53AB, b""), "big") == 0x1C53BB6B:
                        cuespos = base + int.from_bytes(fields[0x53AC], "big")
        offset += size + h
    if cuespos is None or len(init) < 2 or scale <= 0:
        raise SplitError("Selected WebM has no supported cues. Full transfer refused.")
    ident, size, h = header(cuespos)
    if ident != 0x1C53BB6B:
        raise SplitError("Invalid WebM cues pointer.")
    rows = []
    for ident, point in elements(reader.block(cuespos + h, size)):
        if ident != 0xBB:
            continue
        fields = dict(elements(point))
        clock = int.from_bytes(fields[0xB3], "big") * scale
        track = dict(elements(fields[0xB7]))
        position = base + int.from_bytes(track[0xF1], "big")
        if not rows or rows[-1][1] != position:
            rows.append((clock, position))
    # Cues can precede media (YouTube VP9) or follow it (Opus). The last
    # cluster ends at trailing cues only when that index is actually after it.
    final_byte = cuespos if rows and cuespos > rows[-1][1] else reader.size
    result = [(clock, rows[n+1][0] if n+1 < len(rows) else duration,
               byte, (rows[n+1][1] if n+1 < len(rows) else final_byte) - 1)
              for n, (clock, byte) in enumerate(rows)]
    return prefix + b"\x18\x53\x80\x67\x01" + b"\xff" * 7 + b"".join(init), result


def plan(reader, start, end, duration):
    try:
        ext = reader.fmt.get("ext")
        if ext in ("mp4", "m4a"):
            init, rows = mp4_index(reader)
            index_origin = rows[0][0]
            reader.timeline_origin = mp4_presentation_origin(reader, init, rows) if reader.fmt.get("vcodec") not in (None, "", "none") else index_origin
            rows = [(a - index_origin, b - index_origin, first, last)
                    for a, b, first, last in rows]
        elif ext == "webm":
            init, rows = webm_index(reader, duration)
            reader.timeline_origin = 0
        else:
            raise SplitError("Split supports indexed MP4, M4A and WebM streams only.")
        previous_end, previous_byte = -1, -1
        for a, b, first, last in rows:
            if not (0 <= a < b and a >= previous_end - .001 and 0 <= first <= last < reader.size and first > previous_byte):
                raise SplitError("Invalid or unordered media index.")
            previous_end, previous_byte = b, last
        chosen = [row for row in rows if row[1] > max(0, start - 10) and row[0] < min(duration, end + 2)]
        if not chosen or chosen[0][0] > start + .1 or chosen[-1][1] < end - .1:
            raise SplitError("Index does not cover the requested interval.")
        if chosen[0][0] < max(0, start - 40) or chosen[-1][1] > end + 40:
            raise SplitError("Index requires too much boundary overlap; full transfer refused.")
        if any(chosen[n+1][0] - row[1] > .1 for n, row in enumerate(chosen[:-1])):
            raise SplitError("Discontinuous index is not supported.")
        return init, chosen
    except (IndexError, KeyError, struct.error, ZeroDivisionError) as exc:
        raise SplitError("Malformed or unsupported media index.") from exc


def transfer(reader, planned, path):
    init, rows = planned
    total = sum(last - first + 1 for _, _, first, last in rows)
    done = 0
    with open(path, "xb") as stream:
        stream.write(init)
        for _, _, first, last in rows:
            for start in range(first, last + 1, reader.MAX_READ):
                data = reader.read(start, min(start + reader.MAX_READ - 1, last))
                stream.write(data)
                done += len(data)
                yield done, total


def cap_mp4_duration(path, seconds):
    """Cap FFmpeg's single-entry edit lists, retaining decoder dependency frames.

    Reordered frames may be stored beyond the desired endpoint. MP4 edit lists
    control their presentation without changing the encoded samples. Only the
    local moov box is read/written, and its size and sample offsets stay fixed.
    """
    with open(path, "r+b") as stream:
        offset = 0
        size_on_disk = os.fstat(stream.fileno()).st_size
        while offset + 8 <= size_on_disk:
            stream.seek(offset)
            size, kind = struct.unpack(">I4s", stream.read(8))
            header = 8
            if size == 1:
                size, header = struct.unpack(">Q", stream.read(8))[0], 16
            if size < header or offset + size > size_on_disk:
                raise SplitError("Invalid local MP4 box.")
            if kind == b"moov":
                if size > 8 * 1024 * 1024:
                    raise SplitError("Local MP4 metadata is too large.")
                stream.seek(offset)
                data = bytearray(stream.read(size))
                break
            offset += size
        else:
            raise SplitError("Missing local MP4 movie header.")
        scale, tracks, edits = None, 0, 0

        def walk(first, last):
            nonlocal scale, tracks, edits
            pos = first
            while pos < last:
                if pos + 8 > last:
                    raise SplitError("Truncated local MP4 atom.")
                length, kind = struct.unpack_from(">I4s", data, pos)
                body = pos + 8
                if length < 8 or pos + length > last:
                    raise SplitError("Unsupported local MP4 atom.")
                if kind in (b"mvhd", b"tkhd", b"elst"):
                    version = data[body]
                    if version not in (0, 1):
                        raise SplitError("Unsupported local MP4 timing version.")
                    layout = ">Q" if version else ">I"
                    if kind == b"mvhd":
                        at = body + (24 if version else 16)
                        scale = struct.unpack_from(">I", data, at - 4)[0]
                    elif kind == b"tkhd":
                        at = body + (28 if version else 20)
                        tracks += 1
                    else:
                        if struct.unpack_from(">I", data, body + 4)[0] != 1:
                            raise SplitError("Complex MP4 edit lists are unsupported.")
                        at = body + 8
                        edits += 1
                    if not scale:
                        raise SplitError("Missing local MP4 timescale.")
                    old = struct.unpack_from(layout, data, at)[0]
                    struct.pack_into(layout, data, at, min(old, round(seconds * scale)))
                if kind in (b"trak", b"edts"):
                    walk(body, pos + length)
                pos += length

        try:
            walk(header, len(data))
            if not tracks or edits != tracks:
                raise SplitError("Source-preserving timing requires one edit list per track.")
        except (struct.error, IndexError) as exc:
            raise SplitError("Invalid local MP4 timing metadata.") from exc
        stream.seek(offset)
        stream.write(data)


def run(info, index, settings, emit, should_stop=None):
    """Engine entry point: plan both tracks before fetching either media body."""
    import yt_dlp
    from . import engine, selector
    from .naming import build_output_path, safe_title

    start, end = validate(settings.split_start, settings.split_end, info)
    duration = end - start

    def check():
        if should_stop and should_stop():
            raise engine.Cancelled()

    def command(args):
        # Files, not pipes: verbose/broken tools cannot deadlock the worker.
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(args, stdout=stdout, stderr=stderr)
            try:
                while process.poll() is None:
                    check()
                    time.sleep(.05)
                check()
                stderr.seek(0)
                if process.returncode:
                    raise SplitError(stderr.read()[-1500:].decode("utf-8", errors="replace").strip())
                stdout.seek(0)
                return stdout.read()
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()

    def inspect(path):
        return json.loads(command([engine.find_ffprobe(settings), "-v", "error",
            "-show_streams", "-show_format", "-of", "json", path]))

    formats = info.get("formats") or []
    if settings.media_type == "audio":
        video, audio = None, selector.pick_music(formats, settings.mode)
    else:
        video, audio, _ = selector.pick_streams(formats, settings.mode, settings.max_height)
        if selector.acodec_of(video):
            raise SplitError("Split requires separately indexed video/audio tracks for this selection.")
        if selector.codec_family(selector.vcodec_of(video)) not in ("av01", "avc1", "h264", "vp9"):
            raise SplitError("Selected video codec cannot preserve split timing in MP4.")
    if audio and selector.codec_family(selector.acodec_of(audio)) not in ("mp4a", "opus"):
        raise SplitError("Split currently supports indexed AAC or Opus audio tracks.")
    emit("probe", {"index": index, "title": info.get("title", "Clip"),
        "video": selector.describe(video) if video else "Music / audio only",
        "audio": selector.describe(audio, True) if audio else None})
    emit("warning", {"message": "Split: selected interval plus short decoding overlap only; video clips use MP4 edit-list timing."})
    opts = engine._base_opts(settings, engine.EngineLogger(emit))
    opts.pop("download_archive", None)
    output, output_reserved = None, False
    os.makedirs(os.path.join(settings.state_dir, "work"), exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="split-", dir=os.path.join(settings.state_dir, "work")) as work:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                planned = []
                for label, fmt in (("Video", video), ("Audio", audio)):
                    if fmt is None:
                        continue
                    check()
                    reader = RangeReader(ydl, fmt, settings, check)
                    selected = plan(reader, start, end, float(info["duration"]))
                    planned.append((label, fmt, reader, selected))
                inputs, received = [], 0
                for label, fmt, reader, selected in planned:
                    source = os.path.join(work, label + "." + fmt["ext"])
                    for done, total in transfer(reader, selected, source):
                        emit("progress", {"index": index, "label": label,
                            "percent": f"{100*done/total:.1f}%", "speed": "", "eta": ""})
                    received += reader.received
                    emit("stream_done", {"index": index, "label": label,
                        "size": engine.format_size(reader.received)})
                    raw = inspect(source)
                    kind = label.lower()
                    track = next(s for s in raw["streams"] if s["codec_type"] == kind)
                    raw_start = float(track.get("start_time", raw["format"].get("start_time", 0)))
                    if label == "Video":
                        packets = json.loads(command([engine.find_ffprobe(settings), "-v", "error",
                            "-select_streams", "v:0", "-read_intervals", "%+#4096", "-show_packets",
                            "-show_entries", "packet=pts_time,flags", "-of", "json", source]))
                        keyframe = next((p for p in packets.get("packets", []) if "K" in p.get("flags", "")), None)
                        if keyframe is None:
                            raise SplitError("No decoding keyframe in the selected overlap.")
                        raw_start = float(keyframe["pts_time"])
                    normalized = os.path.join(work, "normalized-" + label + (".mp4" if label == "Video" else ".mka"))
                    command([engine.find_ffmpeg(settings), "-y", "-v", "error", "-i", source,
                        "-map", "0:v:0" if label == "Video" else "0:a:0", "-c", "copy",
                        "-avoid_negative_ts", "make_zero", "-f", "mp4" if label == "Video" else "matroska", normalized])
                    # FFmpeg's input -ss is relative to the normalized file's
                    # start_time already. Adding it twice shifts B-frame video.
                    pad = start + reader.timeline_origin - raw_start
                    if pad < -.1 or pad > 40:
                        raise SplitError("Selected media timestamps do not match its index.")
                    inputs.extend(["-ss", str(max(0, pad)), "-i", normalized])
            title = safe_title(info.get("title") or info.get("id"), max_length=80) + suffix(start, end)
            family = selector.codec_family(selector.acodec_of(audio or {}))
            extension = "mp4" if video else ("mp3" if settings.audio_format == "mp3" else
                {"mp4a": "m4a", "aac": "m4a", "opus": "m4a", "vorbis": "mka"}.get(family, "mka"))
            output = build_output_path(settings.media_output_dir, index, title, extension, numbered=bool(settings.numbered))
            with open(output, "xb"):
                pass
            output_reserved = True
            maps = (["-map", "0:v:0"] + (["-map", "1:a:0"] if audio else [])) if video else ["-map", "0:a:0"]
            encoding = ["-c", "copy"]
            if not video and settings.audio_format == "mp3":
                encoding = ["-c:a", "libmp3lame"] + (["-b:a", f"{settings.audio_bitrate}k"]
                    if settings.audio_bitrate else ["-q:a", "0"])
            command([engine.find_ffmpeg(settings), "-y", "-v", "error"] + inputs +
                ["-t", str(duration)] + maps + encoding +
                ["-avoid_negative_ts", "disabled"] + (["-f", "mp4", "-use_editlist", "1", "-movflags", "+faststart"]
                    if extension in ("mp4", "m4a") else []) + [output])
            if extension in ("mp4", "m4a"):
                cap_mp4_duration(output, duration)
            check()
            report = engine.verify_output(output, video, audio, engine.find_ffprobe(settings)) if video else engine.verify_audio_output(output, audio, settings)
            data = inspect(output)
            for track in data["streams"]:
                if track.get("codec_type") not in ("video", "audio"):
                    continue
                begin = float(track.get("start_time", data["format"].get("start_time", 0)))
                length = float(track.get("duration", data["format"].get("duration", 0)))
                tolerance = max(.05, 1 / (selector.fps_of(video) or 25) + .01) if track["codec_type"] == "video" else .075
                if abs(begin) > .05 or abs(length - duration) > tolerance:
                    raise SplitError(f"Cannot preserve split timing ({track['codec_type']}: start {begin:.3f}s, duration {length:.3f}s; requested {duration:.3f}s). No full-file fallback was used.")
            report.update(path=output, split_start=start, split_end=end,
                          received_media_and_index_bytes=received)
            emit("merged", {"index": index, "report": report})
            return report
        except BaseException:
            if output_reserved and output and os.path.isfile(output):
                os.remove(output)
            raise
