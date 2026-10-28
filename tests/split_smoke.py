"""Real indexed transfer + trim over a local HTTP range server; no YouTube needed.

Requires a full FFmpeg for synthetic fixture generation. --ffmpeg/--ffprobe
can point at the minimal bundled tools; --generator supplies the full FFmpeg.
"""
import argparse
import json
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
from unittest import mock

from ytduo import engine
from ytduo.config import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg"))
    parser.add_argument("--ffprobe", default=shutil.which("ffprobe"))
    parser.add_argument("--generator", default=shutil.which("ffmpeg"))
    args = parser.parse_args()
    assert all((args.ffmpeg, args.ffprobe, args.generator)), "FFmpeg/FFprobe required"

    def command(*parts):
        result = subprocess.run(list(parts), capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr
        return result.stdout

    with tempfile.TemporaryDirectory(prefix="ytduo-split-check-") as folder:
        root = Path(folder)
        for name, codec, extra in (("video.mp4", "libx264", ["-g", "20", "-movflags", "dash+global_sidx", "-frag_duration", "2000000"]),
                                    ("video.webm", "libvpx-vp9", ["-g", "20", "-cluster_time_limit", "2000", "-reserve_index_space", "65536"])):
            command(args.generator, "-y", "-v", "error", "-f", "lavfi", "-i",
                "testsrc2=size=64x64:rate=10", "-t", "120", "-an", "-c:v", codec,
                *extra, str(root / name))
        for name, codec, extra in (("audio.m4a", "aac", ["-movflags", "dash+global_sidx", "-frag_duration", "2000000"]),
                                    ("audio.webm", "libopus", ["-cluster_time_limit", "2000"])):
            command(args.generator, "-y", "-v", "error", "-f", "lavfi", "-i",
                "sine=frequency=440:sample_rate=48000", "-t", "120", "-vn", "-c:a", codec,
                *extra, str(root / name))
        received = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                name = self.path.strip("/")
                match = re.fullmatch(r"bytes=(\d+)-(\d+)", self.headers.get("Range", ""))
                if name not in ("video.mp4", "video.webm", "audio.m4a", "audio.webm") or not match:
                    self.send_error(400)
                    return
                data = (root / name).read_bytes()
                start, end = map(int, match.groups())
                body = data[start:end + 1]
                received.append((name, start, end, len(body)))
                self.send_response(206)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def fmt(name, video):
                return {"format_id": name, "ext": name.split(".")[1], "protocol": "https",
                    "url": f"http://127.0.0.1:{server.server_port}/{name}",
                    "filesize": (root / name).stat().st_size, "height": 64 if video else None,
                    "width": 64 if video else None, "fps": 10 if video else None,
                    "vcodec": ("avc1" if name.endswith("mp4") else "vp9") if video else "none",
                    "acodec": "none" if video else ("mp4a.40.2" if name.endswith("m4a") else "opus"), "abr": 128}
            cases = (("video.mp4", "audio.webm", "video", "original"),
                     ("video.webm", "audio.m4a", "video", "original"),
                     (None, "audio.webm", "audio", "original"),
                     (None, "audio.m4a", "audio", "mp3"))
            for n, (video_name, audio_name, media, encoding) in enumerate(cases):
                formats = ([fmt(video_name, True)] if video_name else []) + [fmt(audio_name, False)]
                info = {"id": "split-test", "title": f"Synthetic {n}", "duration": 120, "formats": formats}
                settings = Settings(root=str(root), ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe,
                    split_start=53.25, split_end=58.25, require_supported_host=False,
                    media_type=media, audio_format=encoding, use_archive=False)
                before = len(received)
                with mock.patch.object(engine, "probe", return_value=info), \
                     mock.patch.object(engine, "fetch_stream", side_effect=AssertionError("Full fetch forbidden")):
                    summary = engine.run_batch(["synthetic"], settings)
                assert summary["completed"] == 1, summary
                output = summary["results"][0]["report"]["path"]
                data = json.loads(command(args.ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", output))
                assert abs(float(data["format"]["duration"]) - 5) < .25, data
                body_bytes = sum(r[3] for r in received[before:])
                full_bytes = sum(f["filesize"] for f in formats)
                assert body_bytes < full_bytes * .5, (body_bytes, full_bytes)
                # Full decoding of both clip tracks; no packet loss or invalid fragments.
                errors = subprocess.run([args.generator, "-v", "error", "-i", output, "-f", "null", "-"], capture_output=True, text=True)
                assert errors.returncode == 0 and not errors.stderr.strip(), errors.stderr
                if video_name:
                    def frames(path, at, duration=None):
                        # Decode then select, avoiding the source demuxer's own
                        # random-seek approximation when validating our cut.
                        text = command(args.generator, "-v", "error", "-i", path, "-ss", str(at),
                            *(["-t", str(duration)] if duration else []), "-an", "-f", "framemd5", "-")
                        return [line.split(",")[-1].strip() for line in text.splitlines() if line and not line.startswith("#")]
                    source_frames = frames(str(root / video_name), 0)
                    expected_frames = source_frames[math.ceil(53.25 * 10):math.ceil(58.25 * 10)]
                    actual_frames = frames(output, 0)
                    assert actual_frames == expected_frames, (video_name, len(actual_frames), len(expected_frames))
                print(f"PASS {video_name or 'audio only'} + {audio_name} / {encoding}: {body_bytes}/{full_bytes} bytes, 5 s, full decode", flush=True)
            assert not list((root / ".ytduo/work").glob("split-*")), "Temporary split files leaked"
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    main()
