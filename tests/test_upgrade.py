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
