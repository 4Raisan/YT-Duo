"""Live end-to-end verification against real URLs.

Downloads, merges with stream copy, validates with ffprobe, and reports the
measured result. This is the only test that touches the network.

    python smoke_test.py                     # built-in URLs, default mode
    python smoke_test.py -m compat           # named mode
    python smoke_test.py https://youtu.be/ID # one URL
"""

import argparse
import os
import sys

from ytduo import engine, selector
from ytduo.config import Settings


TEST_URLS = [
    "https://www.youtube.com/watch?v=GczjVOpaPPI",
    "https://www.youtube.com/watch?v=X98-gKKR4Bk",
    "https://www.youtube.com/watch?v=8qZELzy3-LY",
    "https://www.youtube.com/watch?v=4rWx4cBh684",
    "https://www.youtube.com/watch?v=tG1gzsHM5m8",
    "https://www.youtube.com/watch?v=ULviSuJu4Tk",
    "https://www.youtube.com/watch?v=1RXejVzyM4U",
    "https://www.youtube.com/watch?v=f9hMDlqPMf4",
]


def run(url, index, settings):
    info = engine.probe(url, settings)
    video_fmt, audio_fmt, _ = selector.pick_streams(
        info.get("formats") or [], settings.mode)

    print(f"    video  {selector.describe(video_fmt)}")
    print(f"    audio  {selector.describe(audio_fmt, True) if audio_fmt else 'none'}")

    video_path, _ = engine.fetch_stream(
        url, video_fmt, os.path.join(settings.video_dir, "%(id)s_video.%(ext)s"),
        "Video", settings, index)
    audio_path = None
    if audio_fmt:
        audio_path, _ = engine.fetch_stream(
            url, audio_fmt, os.path.join(settings.audio_dir, "%(id)s_audio.%(ext)s"),
            "Audio", settings, index)

    report = engine.merge(index, info.get("title", info["id"]), video_path,
                          audio_path, video_fmt, audio_fmt, settings)

    size_mb = 0.0
    out = report.get("path")
    if out and os.path.isfile(out):
        size_mb = os.path.getsize(out) / (1024 * 1024)

    print(f"    RESULT {report['resolution']} @ {report['fps']}fps"
          f" | {report['video_codec']} + {report['audio_codec']}"
          f" | {report['audio_streams']} audio stream(s)"
          f" | {report['duration']}s | {size_mb:.1f} MB")
    return report


def main():
    parser = argparse.ArgumentParser(description="YT-Duo live smoke test")
    parser.add_argument("-m", "--mode", default="best", choices=sorted(selector.MODES))
    parser.add_argument("urls", nargs="*", default=None)
    args = parser.parse_args()

    settings = Settings(mode=args.mode)
    settings.ensure_dirs()

    urls = args.urls or TEST_URLS

    print(f"mode={settings.mode}")
    print(f"ffmpeg={engine.find_ffmpeg(settings)}")
    print(f"ffprobe={engine.find_ffprobe(settings)}\n")

    ok = failed = 0
    for index, url in enumerate(urls, 1):
        print(f"  [{index}/{len(urls)}] {url}")
        try:
            run(url, index, settings)
            ok += 1
        except Exception as exc:
            print(f"    FAILED  {engine.classify_error(exc)}")
            failed += 1
        print()

    print(f"smoke: {ok} ok, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
