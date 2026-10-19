"""Offline coverage for music, metadata feedback, folder actions, and updates."""
import io
import json
import os
from pathlib import Path
import subprocess
import threading
import urllib.error
import unittest
from unittest import mock

from ytduo import engine, selector
from ytduo.config import Settings
from tests.test_engine import make_workspace
from tests.test_gui import GuiTestCase, pump_until


FORMATS = [
    {"format_id": "v1080", "vcodec": "vp9", "acodec": "none", "height": 1080,
     "fps": 30, "vbr": 2000, "protocol": "https", "ext": "webm"},
    {"format_id": "v720", "vcodec": "vp9", "acodec": "none", "height": 720,
     "fps": 30, "vbr": 1000, "protocol": "https", "ext": "webm"},
    {"format_id": "audio", "vcodec": "none", "acodec": "opus", "abr": 130,
     "protocol": "https", "ext": "webm"},
]
INFO = {"id": "example", "title": "Example", "formats": FORMATS}




class MusicPipeline(unittest.TestCase):
    pass

    def setUp(self):
        self.root = Path(make_workspace())
        self.settings = Settings(root=str(self.root), media_type="audio",
                                 ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")
        self.settings.ensure_dirs()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_default_remains_video_and_copies_keep_audio_settings(self):
        self.assertEqual(Settings().media_type, "video")
        self.assertEqual(self.settings.replace(audio_format="mp3").media_type, "audio")

    def test_muxed_fallback_and_no_silent_video_only_music(self):
        muxed = dict(FORMATS[0], acodec="mp4a.40.2", abr=128)
        self.assertEqual(selector.pick_music([muxed]), muxed)
        with self.assertRaises(selector.NoSuitableFormat):
            selector.pick_music([FORMATS[0]])

    def test_no_drm_or_drc_music(self):
        for field in ({"has_drm": True}, {"format_note": "DRC"}):
            with self.assertRaises(selector.NoSuitableFormat):
                selector.pick_music([dict(FORMATS[2], **field)])

    def test_archives_separate_video_original_and_mp3(self):
        paths = {self.settings.archive_file,
                 self.settings.replace(media_type="video").archive_file,
                 self.settings.replace(audio_format="mp3").archive_file}
        self.assertEqual(len(paths), 3)
        self.assertNotEqual(self.settings.replace(audio_format="mp3", audio_bitrate=192).archive_file,
                            self.settings.replace(audio_format="mp3", audio_bitrate=320).archive_file)

    def test_audio_success_does_not_archive_video(self):
        output = Path(self.settings.output_dir) / "music.opus"
        output.write_bytes(b"verified")
        with mock.patch.object(engine, "is_host_supported", return_value=True), \
             mock.patch.object(engine, "probe", return_value=INFO), \
             mock.patch.object(engine, "run_music", return_value={"path": str(output)}):
            engine.run_item("https://example.com/watch", 1, 1, self.settings)
        self.assertTrue(engine.already_downloaded("https://example.com/watch", self.settings))
        self.assertFalse(engine.already_downloaded("https://example.com/watch",
                         self.settings.replace(media_type="video")))

    def test_original_copy_and_mp3_conversion(self):
        for conversion in ("original", "mp3"):
            settings = self.settings.replace(audio_format=conversion)
            source = Path(settings.audio_dir) / (conversion + ".webm")
            source.write_bytes(b"source")
            with mock.patch.object(engine, "fetch_stream", return_value=(str(source), INFO)), \
                 mock.patch.object(engine.subprocess, "run", return_value=mock.Mock(returncode=0)) as run, \
                 mock.patch.object(engine, "verify_audio_output", return_value={"path": "verified"}):
                engine.run_music("https://example.com/watch", 1, "Music", FORMATS,
                                 settings, lambda *args: None)
            args = run.call_args.args[0]
            self.assertEqual(args[args.index("-map") + 1], "0:a:0")
            self.assertIn("-vn", args)
            self.assertEqual(args[args.index("-c:a") + 1],
                             "copy" if conversion == "original" else "libmp3lame")
            self.assertTrue(args[-1].endswith(".opus" if conversion == "original" else ".mp3"))
            self.assertEqual(Path(args[-1]).parent, Path(settings.output_dir) / "Audio")
            self.assertFalse(source.exists())

    def test_failed_verification_removes_output_but_retains_source(self):
        source = Path(self.settings.audio_dir) / "source.webm"
        source.write_bytes(b"source")
        with mock.patch.object(engine, "fetch_stream", return_value=(str(source), INFO)), \
             mock.patch.object(engine.subprocess, "run", return_value=mock.Mock(returncode=0)), \
             mock.patch.object(engine, "verify_audio_output", side_effect=engine.ValidationError("bad")):
            with self.assertRaises(engine.ValidationError):
                engine.run_music("https://example.com/watch", 1, "Music", FORMATS,
                                 self.settings, lambda *args: None)
        self.assertTrue(source.exists())
        self.assertEqual(list(Path(self.settings.output_dir).glob("*.opus")), [])
        self.assertFalse(Path(self.settings.archive_file).exists())

    def test_output_collision_never_removes_existing_file(self):
        source = Path(self.settings.audio_dir) / "source.webm"
        source.write_bytes(b"source")
        existing = Path(self.settings.output_dir) / "existing.opus"
        existing.write_bytes(b"keep")
        with mock.patch.object(engine, "fetch_stream", return_value=(str(source), INFO)), \
             mock.patch.object(engine, "build_output_path", return_value=str(existing)):
            with self.assertRaises(FileExistsError):
                engine.run_music("https://example.com/watch", 1, "Music", FORMATS,
                                 self.settings, lambda *args: None)
        self.assertEqual(existing.read_bytes(), b"keep")

    def test_cancellation_does_not_leave_reserved_output(self):
        source = Path(self.settings.audio_dir) / "source.webm"
        source.write_bytes(b"source")
        with mock.patch.object(engine, "fetch_stream", return_value=(str(source), INFO)):
            with self.assertRaises(engine.Cancelled):
                engine.run_music("https://example.com/watch", 1, "Music", FORMATS,
                                 self.settings, lambda *args: None, should_stop=lambda: True)
        self.assertEqual(list(Path(self.settings.output_dir).glob("*.opus")), [])
        self.assertTrue(source.exists())

    def test_audio_output_checks_codec_duration_and_video_absence(self):
        good = {"streams": [{"codec_type": "audio", "codec_name": "opus"}],
                "format": {"duration": "4.2"}}
        with mock.patch.object(engine.subprocess, "run", return_value=mock.Mock(
                returncode=0, stdout=json.dumps(good))):
            result = engine.verify_audio_output("x.opus", FORMATS[2], self.settings)
        self.assertEqual(result["media_type"], "audio")
        for data in (
            dict(good, streams=good["streams"] + [{"codec_type": "video"}]),
            dict(good, format={"duration": 0}),
            dict(good, streams=[{"codec_type": "audio", "codec_name": "aac"}]),
        ):
            with mock.patch.object(engine.subprocess, "run", return_value=mock.Mock(
                    returncode=0, stdout=json.dumps(data))):
                with self.assertRaises(engine.ValidationError):
                    engine.verify_audio_output("x.opus", FORMATS[2], self.settings)

    def test_explicit_mp3_quality_is_verified(self):
        settings = self.settings.replace(audio_format="mp3", audio_bitrate=192)
        for actual, valid in ((192000, True), (128000, False)):
            data = {"streams": [{"codec_type": "audio", "codec_name": "mp3", "bit_rate": str(actual)}],
                    "format": {"duration": "3"}}
            with mock.patch.object(engine.subprocess, "run", return_value=mock.Mock(
                    returncode=0, stdout=json.dumps(data))):
                if valid:
                    result = engine.verify_audio_output("x.mp3", FORMATS[2], settings)
                    self.assertEqual(result["audio_bitrate"], 192000)
                else:
                    with self.assertRaises(engine.ValidationError):
                        engine.verify_audio_output("x.mp3", FORMATS[2], settings)
