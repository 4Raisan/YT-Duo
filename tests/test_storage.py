"""Private state and lossless migration on portable and installed layouts."""
import json
import errno
import os
from pathlib import Path
from unittest import mock

from ytduo import config, engine
from tests.test_engine import TempWorkspace


class PrivateStorage(TempWorkspace):
    def test_defaults_keep_work_and_history_private_and_media_visible(self):
        settings = self.settings
        for path in (settings.config_file, settings.video_dir, settings.audio_dir,
                     settings.recycle_dir, settings.archive_file):
            self.assertTrue(Path(path).is_relative_to(Path(settings.state_dir)))
        self.assertEqual(Path(settings.output_dir).name, "YT-Duo Downloads")
        self.assertEqual(Path(settings.replace(media_type="audio").media_output_dir).name, "Audio")
        self.assertFalse((Path(self.root) / "video").exists())
        self.assertFalse((Path(self.root) / "audio").exists())
        self.assertFalse((Path(self.root) / "config.json").exists())
        if os.name == "nt":
            import ctypes
            self.assertTrue(ctypes.windll.kernel32.GetFileAttributesW(settings.state_dir) & 2)

    def test_migration_preserves_preferences_partials_and_finished_media(self):
        root = Path(self.root)
        saved = {"theme": "pink", "output_dir": self.settings.output_dir, "font_scale": 110}
        (root / "config.json").write_text(json.dumps(saved))
        self.write(str(root / "video/unfinished.part"), b"partial")
        finished = self.write(str(Path(self.settings.output_dir) / "Keep.mp4"), b"media")
        # ensure_dirs already created the new work directory: collision is retained.
        loaded = config.read_user_config(self.settings)
        self.assertEqual(loaded, saved)
        self.assertFalse((root / "config.json").exists())
        self.assertFalse((root / "video").exists())
        self.assertEqual(next((root / ".ytduo/work").glob("video*/unfinished.part")).read_bytes(), b"partial")
        self.assertEqual(Path(finished).read_bytes(), b"media")
        self.assertEqual(config.read_user_config(self.settings), saved)

    def test_existing_private_preferences_are_never_overwritten(self):
        config.write_config(self.settings.config_file, {"theme": "green"})
        old = Path(self.root) / "config.json"
        old.write_text(json.dumps({"theme": "dark", "output_dir": self.settings.output_dir}))
        self.assertEqual(config.read_user_config(self.settings), {"theme": "green"})
        self.assertTrue(Path(self.settings.config_file + ".previous-2").is_file())

    def test_unrecognized_generic_directories_are_not_moved(self):
        self.write(str(Path(self.root) / "audio/personal.txt"), b"keep")
        config.read_user_config(self.settings)
        self.assertTrue((Path(self.root) / "audio/personal.txt").is_file())

    def test_selected_media_folder_is_not_migrated_as_work(self):
        output = Path(self.root) / "audio"
        self.write(str(output / "song.mp3"), b"keep")
        (Path(self.root) / "config.json").write_text(json.dumps({"theme": "dark", "output_dir": str(output)}))
        config.read_user_config(self.settings)
        self.assertEqual((output / "song.mp3").read_bytes(), b"keep")

    def test_read_only_migration_keeps_existing_preferences_usable(self):
        old = Path(self.root) / "config.json"
        saved = {"theme": "dark", "output_dir": self.settings.output_dir}
        old.write_text(json.dumps(saved))
        with mock.patch.object(config, "migrate_legacy_state", side_effect=PermissionError):
            self.assertEqual(config.read_user_config(self.settings), saved)
        self.assertTrue(old.is_file())

    def test_url_only_history_moves_privately_without_claiming_an_exact_match(self):
        legacy = Path(self.settings.output_dir) / ".YT-Duo-archive.txt"
        legacy.write_text("YT-Duo https://youtu.be/example /unqualified/file.mp4\n")
        self.settings.ensure_dirs()
        self.assertFalse(legacy.exists())
        self.assertTrue(list((Path(self.settings.state_dir) / "history").glob("legacy-*")))
        self.assertFalse(engine.already_downloaded("https://youtu.be/example", self.settings))

    def test_custom_work_paths_stay_explicit(self):
        custom = self.settings.replace(video_dir="custom-work")
        custom.ensure_dirs()
        self.assertEqual(custom.video_dir, os.path.join(self.root, "custom-work"))

    def test_history_can_migrate_from_a_different_filesystem(self):
        source = Path(self.settings.output_dir) / ".YT-Duo-archive.txt"
        source.write_bytes(b"old history")
        target = Path(self.settings.state_dir) / "history/old.txt"
        with mock.patch.object(config.os, "rename", side_effect=OSError(errno.EXDEV, "cross-device")):
            config._move_legacy(str(source), str(target))
        self.assertEqual(target.read_bytes(), b"old history")
        self.assertFalse(source.exists())
