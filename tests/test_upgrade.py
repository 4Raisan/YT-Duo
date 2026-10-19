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
