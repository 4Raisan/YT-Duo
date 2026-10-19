"""Regenerate fixtures/formats.json from live yt-dlp metadata.

Usage:  python capture_fixtures.py

Fixtures let test_selector.py run fully offline. Only format-relevant
metadata is stored; no URLs, titles, or credentials.
"""

import json
import os
import sys

import yt_dlp


HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fixtures", "formats.json")

VIDEO_IDS = [
    "X98-gKKR4Bk",
    "8qZELzy3-LY",
    "GczjVOpaPPI",
    "4rWx4cBh684",
    "tG1gzsHM5m8",
    "ULviSuJu4Tk",
    "1RXejVzyM4U",
    "f9hMDlqPMf4",
]

FIELDS = (
    "format_id", "format", "format_index", "ext", "resolution", "width",
    "height", "fps", "vcodec", "vbr", "acodec", "abr", "asr", "tbr",
    "filesize", "filesize_approx", "protocol", "format_note", "language",
    "language_preference", "dynamic_range", "source_preference",
    "quality", "has_drm", "container", "resolution_note",
)


class Quiet:
    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        print("  warn:", msg)

    def error(self, msg):
        print("  error:", msg)


def slim(fmt):
    return {key: fmt[key] for key in FIELDS if key in fmt}


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    bundle = {}

    for vid in VIDEO_IDS:
        url = f"https://www.youtube.com/watch?v={vid}"
        print(f"capturing {vid} ...")
        with yt_dlp.YoutubeDL({
            "quiet": True,
            "no_warnings": True,
            "logger": Quiet(),
            "skip_download": True,
        }) as ydl:
            info = ydl.extract_info(url, download=False)

        bundle[vid] = {
            "id": info.get("id"),
            "duration": info.get("duration"),
            "is_live": bool(info.get("is_live")),
            "formats": [slim(f) for f in (info.get("formats") or [])],
        }
        print(f"  {len(bundle[vid]['formats'])} formats")

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, indent=1, sort_keys=True)
    print(f"\nwrote {OUT} ({os.path.getsize(OUT)} bytes, {len(bundle)} videos)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
