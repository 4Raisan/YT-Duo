"""Run real media tools through the engine with synthetic clips, without network.

python tests/media_smoke.py [--ffmpeg PATH --ffprobe PATH]
"""
import argparse
import base64
import json
from pathlib import Path
import shutil
import tempfile
from unittest import mock

from ytduo import engine
from ytduo.config import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg"))
    parser.add_argument("--ffprobe", default=shutil.which("ffprobe"))
    args = parser.parse_args()
    if not args.ffmpeg or not args.ffprobe:
        parser.error("ffmpeg and ffprobe are required")
    fixtures = json.loads((Path(__file__).parent / "fixtures/media.json").read_text())["clips"]
    conversions = 0
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        settings = Settings(root=folder, ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe)
        settings.ensure_dirs()
        for number, (name, encoded) in enumerate(fixtures.items()):
            vc, ac = ("avc1", "mp4a.40.2") if name.startswith("h264") else (
                "av01" if name.startswith("av1") else "vp9", "opus")
            title = name.split(".")[0]
            fmt = {"format_id": title, "height": 64, "width": 64, "fps": 10,
                   "vcodec": vc, "acodec": ac, "protocol": "https", "abr": 128,
                   "ext": name.rsplit(".", 1)[1]}
            info = {"id": "YtDuoTest00" + str(number), "title": title, "formats": [fmt]}
            url = "https://youtu.be/" + info["id"]
            payload = base64.b64decode(encoded)
            def fetch(url, fmt, target, label, current, index, emit, should_stop):
                path = Path(current.audio_dir if label == "Audio" else current.video_dir) / name
                path.write_bytes(payload)
                return str(path), info
            with mock.patch.object(engine, "probe", return_value=info), \
                 mock.patch.object(engine, "fetch_stream", side_effect=fetch) as transfer:
                confirm = mock.Mock(return_value=False)
                summary = engine.run_batch([url], settings, confirm_redownload=confirm)
                assert summary["completed"] == 1, summary
                video = Path(summary["results"][0]["report"]["path"])
                assert video.name == title + (".mp4" if vc == "avc1" else ".mkv"), video
                conversions += 1
                transfer.reset_mock()
                summary = engine.run_batch([url], settings, confirm_redownload=confirm)
                assert summary["skipped"] == 1, summary
                confirm.assert_called_once_with(url)
                transfer.assert_not_called()
                confirm.reset_mock()
                for rate in ("original", None, 128, 192, 256, 320):
                    current = settings.replace(media_type="audio",
                        audio_format="original" if rate == "original" else "mp3",
                        audio_bitrate=None if rate == "original" else rate)
                    summary = engine.run_batch([url], current, confirm_redownload=confirm)
                    assert summary["completed"] == 1, summary
                    output = Path(summary["results"][0]["report"]["path"])
                    assert output.parent.name == "Audio", output
                    assert not output.name.startswith("01. "), output
                    conversions += 1
                confirm.assert_not_called()
                video.unlink()
                summary = engine.run_batch([url], settings, confirm_redownload=confirm)
                assert summary["completed"] == 1, summary
                confirm.assert_not_called()
                # Failure keeps the following item's original queue position.
                def queued_probe(url, *args):
                    if url == "synthetic-failure":
                        raise ValueError("intentional synthetic queue failure")
                    return dict(info, title=title + " queued")
                with mock.patch.object(engine, "probe", side_effect=queued_probe):
                    summary = engine.run_batch([url, "synthetic-failure", url],
                                               settings.replace(use_archive=False))
                assert summary["completed"] == 2 and summary["failed"] == 1, summary
                outputs = [Path(row["report"]["path"]).name for row in summary["results"] if row["ok"]]
                assert outputs[0].startswith("01. ") and outputs[1].startswith("03. "), outputs
        assert not (root / "audio").exists() and not (root / "video").exists()
        assert not list(Path(settings.output_dir).glob("*archive*"))
        assert Path(settings.archive_file).is_relative_to(Path(settings.state_dir))
    print(f"Passed {conversions} real media conversions, duplicate decisions, queue naming and private storage checks.")


if __name__ == "__main__":
    main()
