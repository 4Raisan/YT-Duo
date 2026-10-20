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

from ytduo import engine, selector, updates
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

    def test_archives_separate_video_original_and_mp3(self):
        settings = [self.settings, self.settings.replace(media_type="video"),
                    self.settings.replace(audio_format="mp3"),
                    self.settings.replace(audio_format="mp3", audio_bitrate=192),
                    self.settings.replace(audio_format="mp3", audio_bitrate=320)]
        self.assertEqual(len({engine._archive_key("url", s) for s in settings}), 5)

    def test_muxed_fallback_and_no_silent_video_only_music(self):
        muxed = dict(FORMATS[0], acodec="mp4a.40.2", abr=128)
        self.assertEqual(selector.pick_music([muxed]), muxed)
        with self.assertRaises(selector.NoSuitableFormat):
            selector.pick_music([FORMATS[0]])

    def test_no_drm_or_drc_music(self):
        for field in ({"has_drm": True}, {"format_note": "DRC"}):
            with self.assertRaises(selector.NoSuitableFormat):
                selector.pick_music([dict(FORMATS[2], **field)])

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

    def test_audio_success_does_not_archive_video(self):
        output = Path(self.settings.output_dir) / "music.opus"
        output.write_bytes(b"verified")
        with mock.patch.object(engine, "is_host_supported", return_value=True), \
             mock.patch.object(engine, "probe", return_value=INFO), \
             mock.patch.object(engine, "run_music", return_value={"path": str(output)}):
            engine.run_item("https://example.com/watch", 1, 1, self.settings)
        self.assertTrue(engine.already_downloaded("https://example.com/watch", self.settings,
                         engine._selection_identity(FORMATS, self.settings)))
        self.assertFalse(engine.already_downloaded("https://example.com/watch",
                         self.settings.replace(media_type="video")))


class ReleaseChecks(unittest.TestCase):
    def checker(self, release):
        opener = mock.Mock()
        opener.open.return_value.__enter__ = mock.Mock(return_value=io.BytesIO(json.dumps(release).encode()))
        opener.open.return_value.__exit__ = mock.Mock(return_value=False)
        return updates.UpdateChecker("4.0.0", opener=opener, clock=lambda: 10), opener

    def test_only_numeric_stable_versions_and_fixed_release_link(self):
        checker, opener = self.checker({"tag_name": "v10.0.0", "html_url": "https://evil.invalid"})
        result = checker.check()
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["url"], updates.RELEASE_URL)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, updates.RELEASE_API)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 8)

    def test_drafts_prereleases_and_invalid_tags_are_not_updates(self):
        for release in ({"tag_name": "v5.0.0", "draft": True},
                        {"tag_name": "v5.0.0", "prerelease": True},
                        {"tag_name": "v5.0.0-beta"}, {"tag_name": "main"}, []):
            checker, _ = self.checker(release)
            self.assertEqual(checker.check()["status"], "unavailable")

    def test_current_cache_and_explicit_refresh(self):
        checker, opener = self.checker({"tag_name": "v4.0.0"})
        self.assertEqual(checker.check()["status"], "current")
        checker.check()
        self.assertEqual(opener.open.call_count, 1)
        # A fresh stream is required for an explicit second HTTP response.
        opener.open.return_value.__enter__.return_value = io.BytesIO(b'{"tag_name":"v4.0.0"}')
        checker.check(force=True)
        self.assertEqual(opener.open.call_count, 2)

    def test_offline_and_missing_release_are_not_current(self):
        checker, opener = self.checker({})
        opener.open.side_effect = OSError("offline")
        self.assertEqual(checker.check()["status"], "unavailable")
        opener.open.side_effect = urllib.error.HTTPError(updates.RELEASE_API, 404, "missing", {}, None)
        self.assertEqual(checker.check(force=True)["status"], "unpublished")

    def test_rate_limited_api_uses_only_trusted_stable_release_redirect(self):
        for location, expected in ((updates.REPO_URL + "/releases/tag/v5.0.0", "available"),
                                   ("https://evil.invalid/releases/tag/v5.0.0", "unavailable"),
                                   (updates.REPO_URL + "/releases/tag/v5.0.0-beta", "unavailable")):
            checker, opener = self.checker({})
            opener.open.side_effect = [
                urllib.error.HTTPError(updates.RELEASE_API, 403, "rate limited", {}, None),
                urllib.error.HTTPError(updates.RELEASE_URL, 302, "redirect", {"Location": location}, None)]
            result = checker.check(True)
            self.assertEqual(result["status"], expected)
            self.assertEqual(opener.open.call_args.args[0].full_url, updates.RELEASE_URL)

    def test_offline_reason_is_actionable_and_retry_is_not_cached_for_an_hour(self):
        checker, opener = self.checker({})
        opener.open.side_effect = OSError("private details")
        self.assertIn("connection", checker.check()["reason"])
        checker.clock = lambda: 71
        checker.check()
        self.assertEqual(opener.open.call_count, 4)


class FolderPolicy(unittest.TestCase):
    def test_installed_and_portable_defaults_and_custom_destinations(self):
        from ytduo import config
        with mock.patch.object(config, "project_root", return_value=os.path.abspath("test-data")), \
             mock.patch.object(config, "user_downloads_folder", return_value=os.path.abspath("user-downloads")), \
             mock.patch.dict(os.environ, {"YTDUO_INSTALLED": "1"}):
            self.assertEqual(Settings().output_dir, os.path.abspath("user-downloads/YT-Duo Downloads"))
            custom = Settings(output_dir=os.path.abspath("custom"), media_type="audio")
            self.assertEqual(custom.media_output_dir, os.path.abspath("custom/Audio"))
            with mock.patch.dict(os.environ, {"YTDUO_INSTALLED": "0"}):
                self.assertEqual(Settings().output_dir, os.path.abspath("test-data/YT-Duo Downloads"))

    def test_recycle_cleanup_is_scoped_and_does_not_remove_finished_media(self):
        import shutil
        root = Path(make_workspace())
        try:
            settings = Settings(root=str(root))
            recycle = Path(settings.recycle_dir) / "nested"
            recycle.mkdir(parents=True)
            (recycle / "old.tmp").write_bytes(b"cache")
            kept = root / "finished.mp4"
            kept.write_bytes(b"finished")
            engine.clear_recycle(settings)
            self.assertFalse(Path(settings.recycle_dir).exists())
            with self.assertRaises(OSError):
                engine.clear_recycle(settings.replace(recycle_dir=str(root)))
            self.assertEqual(kept.read_bytes(), b"finished")
            with mock.patch.object(engine.os.path, "islink", return_value=True):
                with self.assertRaises(OSError):
                    engine.clear_recycle(settings)
        finally:
            shutil.rmtree(root, ignore_errors=True)


class UpgradeGui(GuiTestCase):
    def test_main_constructs_hidden_and_first_mapping_is_transparent_and_complete(self):
        import tkinter as tk
        from ytduo import gui
        root = tk.Tk()
        built, maps, visible = [], [], []
        def mapped(event):
            if event.widget is root:
                maps.append((float(root.attributes("-alpha")), bool(built)))
        root.bind("<Map>", mapped, add="+")
        original = gui.YtDuoApp
        def build(window):
            app = original(window, background_checks=False)
            built.append(app)
            return app
        def loop():
            root.update()
            visible.append((root.winfo_width(), root.winfo_height()))
            root.update()
            self.assertEqual(visible[0], (root.winfo_width(), root.winfo_height()))
            self.assertEqual(float(root.attributes("-alpha")), 1.0)
            self.assertTrue(built[0].footer_row.winfo_ismapped())
            self.assertTrue(built[0].single_button.winfo_ismapped())
        try:
            with mock.patch.object(gui.tk, "Tk", return_value=root), \
                 mock.patch.object(gui, "YtDuoApp", side_effect=build), \
                 mock.patch.object(original, "_config_path", return_value=self.config_path), \
                 mock.patch.object(root, "mainloop", side_effect=loop):
                self.assertEqual(gui.main(), 0)
            self.assertTrue(maps)
            self.assertTrue(all(alpha == 0 and complete for alpha, complete in maps), maps)
        finally:
            for app in built:
                app.destroy()
            root.destroy()

    def test_old_default_migrates_once_and_explicit_legacy_choice_is_preserved(self):
        import tkinter as tk
        from ytduo.gui import YtDuoApp
        legacy = os.path.join(Settings().root, "downloads")
        for marked in (False, True):
            Path(self.config_path).write_text(json.dumps({"output_dir": legacy,
                "destination_layout": int(marked)}))
            root = tk.Tk()
            root.withdraw()
            app = None
            try:
                with mock.patch.object(YtDuoApp, "_config_path", return_value=self.config_path):
                    app = YtDuoApp(root, background_checks=False)
                self.assertEqual(app.output_var.get(), legacy if marked else Settings().output_dir)
            finally:
                if app:
                    app.destroy()
                root.destroy()

    def test_empty_download_is_silent_and_terminal_height_stays_fixed(self):
        with mock.patch("ytduo.gui.messagebox.showinfo") as popup:
            self.app.download_single()
            popup.assert_not_called()
        height = self.app.log.cget("height")
        for _ in range(30):
            self.app.append_log("more output")
        self.assertEqual(self.app.log.cget("height"), height)
        self.app._set_running(True)
        self.assertEqual(self.tk.cget("cursor"), "")

    def test_scanning_animates_then_stops_for_invalid_and_ready_links(self):
        self.app.url_var.set("https://youtu.be/abcdefghijk")
        self.assertTrue(self.app._scan_active)
        self.assertIsNotNone(self.app._scan_anim_id)
        self.metadata()
        self.assertFalse(self.app._scan_active)
        self.assertIsNone(self.app._scan_anim_id)
        self.app.url_var.set("not a link")
        self.assertFalse(self.app._scan_active)

    def test_music_destination_and_open_use_audio_child_of_custom_root(self):
        self.app._on_media("audio")
        expected = os.path.join(self.root, "Audio")
        self.assertEqual(self.app._make_settings().media_output_dir, expected)
        self.assertEqual(self.app.dest_label._full_path, expected)
        with mock.patch.object(self.app, "_open_folder_action") as open_folder:
            self.app.open_output()
            open_folder.assert_called_once_with(expected)

    def test_unavailable_destination_offers_custom_folder(self):
        with mock.patch("ytduo.gui.os.makedirs", side_effect=[PermissionError(), None]), \
             mock.patch("tkinter.filedialog.askdirectory", return_value=self.root) as browse:
            self.assertTrue(self.app._preflight(self.app._make_settings()))
            browse.assert_called_once()

    def test_existing_but_unwritable_destination_also_offers_custom_folder(self):
        with mock.patch("ytduo.gui.tempfile.TemporaryFile",
                        side_effect=[PermissionError(), mock.MagicMock()]) as probe, \
             mock.patch("tkinter.filedialog.askdirectory", return_value=self.root) as browse:
            self.assertTrue(self.app._preflight(self.app._make_settings()))
            self.assertEqual(probe.call_count, 2)
            browse.assert_called_once()

    def test_each_download_opens_terminal_and_controls_belong_to_progress(self):
        self.app.toggle_settings(True)
        with mock.patch.object(engine, "run_batch", return_value={}) as run:
            self.app._start(["https://youtu.be/abcdefghijk"])
            self.assertTrue(self.app._log_visible)
            self.assertFalse(self.app.settings_visible)
            self.assertIs(self.app.cancel_button.master, self.app.run_controls)
            self.assertIs(self.app.run_controls.master, self.app.progress_line)
            self.assertEqual(self.app.cancel_button.text, "■")
            self.assertTrue(pump_until(self.tk, lambda: self.app.worker is None))
        self.app.toggle_log(False)
        self.app._on_item_start({"index": 2, "total": 2})
        self.assertTrue(self.app._log_visible)

    def test_duplicate_timeout_skips_without_downloading(self):
        with mock.patch.object(engine, "already_downloaded", return_value=True), \
             mock.patch.object(engine, "probe", return_value=INFO), \
             mock.patch.object(engine, "fetch_stream") as fetch:
            self.app._start(["https://youtu.be/abcdefghijk"])
            self.assertTrue(pump_until(self.tk, lambda: self.app._redownload_dialog is not None))
            self.assertIn("10s", self.app._redownload_countdown.cget("text"))
            self.assertTrue(pump_until(self.tk, lambda: self.app.worker is None, timeout=12))
            fetch.assert_not_called()
            self.assertIsNone(self.app._redownload_dialog)
            self.assertIn("1 skipped", self.app.status_label.cget("text"))

    def test_duplicate_redownload_confirmation_and_stop_release_worker(self):
        for approve in (True, False):
            with mock.patch.object(engine, "already_downloaded", return_value=True), \
                 mock.patch.object(engine, "probe", return_value=INFO), \
                 mock.patch.object(engine, "fetch_stream", return_value=("synthetic", INFO)), \
                 mock.patch.object(engine, "merge", return_value={"path": "verified"}) as merge:
                self.app._start(["https://youtu.be/abcdefghijk"])
                self.assertTrue(pump_until(self.tk, lambda: self.app._redownload_dialog is not None))
                if approve:
                    self.app._finish_redownload(True)
                else:
                    self.app.cancel()
                self.assertTrue(pump_until(self.tk, lambda: self.app.worker is None))
                self.assertEqual(merge.call_count, int(approve))
                self.assertIsNone(self.app._redownload_dialog)

    def test_pause_resume_and_stop_do_not_block_tk(self):
        import time
        entered = threading.Event()
        steps = [0]
        def slow(urls, settings, emit, should_stop, **kwargs):
            entered.set()
            while not should_stop():
                steps[0] += 1
                time.sleep(0.01)
            return {"cancelled": True}
        with mock.patch.object(engine, "run_batch", side_effect=slow):
            self.app._start(["https://youtu.be/abcdefghijk"])
            self.assertTrue(entered.wait(1))
            self.app.toggle_pause()
            self.assertTrue(pump_until(self.tk, lambda: self.app.status_label.cget("text") == "Paused"))
            self.assertEqual(self.app.pause_button.text, "▶")
            paused_steps = steps[0]
            self.app.toggle_pause()
            self.assertEqual(self.app.pause_button.text, "Ⅱ")
            self.assertTrue(pump_until(self.tk, lambda: steps[0] > paused_steps))
            self.app.toggle_pause()
            self.app.cancel()
            self.assertTrue(pump_until(self.tk, lambda: self.app.worker is None))
            self.assertFalse(self.app.pause_button._active)

    def test_auto_download_waits_for_valid_scan_and_uses_current_choices_once(self):
        self.assertFalse(self.app.auto_download_var.get())
        self.app.auto_download_var.set(True)
        self.app._on_media("audio")
        self.app._on_audio_quality(192)
        self.app.url_var.set("https://youtu.be/abcdefghijk")
        generation = self.app._preview_generation
        with mock.patch.object(self.app, "_start", return_value=True) as start:
            self.app._maybe_auto_download()
            start.assert_not_called()
            self.app._on_preview_result({"generation": generation, "info": INFO})
            start.assert_called_once_with(["https://youtu.be/abcdefghijk"])
            self.assertEqual(self.app.url_var.get(), "")
            self.app._on_preview_result({"generation": generation, "info": INFO})
            self.assertEqual(start.call_count, 1)
        settings = self.app._make_settings()
        self.assertEqual((settings.media_type, settings.audio_format, settings.audio_bitrate),
                         ("audio", "mp3", 192))

    def test_auto_download_ignores_disabled_stale_invalid_and_failed_scans(self):
        self.app.url_var.set("https://youtu.be/abcdefghijk")
        generation = self.app._preview_generation
        with mock.patch.object(self.app, "download_single") as download:
            self.metadata()
            download.assert_not_called()
            self.app.auto_download_var.set(True)
            self.app.url_var.set("not a link")
            self.app._on_preview_result({"generation": generation, "info": INFO})
            self.app._maybe_auto_download()
            self.app.url_var.set("https://youtu.be/newabcdefgh")
            self.app._on_preview_result({"generation": self.app._preview_generation,
                                         "error": "network timeout"})
            self.app._maybe_auto_download()
            download.assert_not_called()

    def test_enabling_auto_after_scan_downloads_and_running_batch_accepts_new_paste(self):
        url = "https://youtu.be/abcdefghijk"
        self.app.url_var.set(url)
        self.metadata()
        self.app.worker = mock.Mock()
        self.app.worker.is_alive.return_value = True
        self.app.auto_download_var.set(True)
        self.app._maybe_auto_download()
        self.assertEqual(self.app._take_pending_links(), [url])
        self.assertEqual(self.app.url_var.get(), "")
        self.app.worker = None

    def test_failed_preflight_keeps_auto_link_for_manual_retry_without_repeating(self):
        url = "https://youtu.be/abcdefghijk"
        self.app.auto_download_var.set(True)
        self.app.url_var.set(url)
        with mock.patch.object(self.app, "_preflight", return_value=False) as check:
            self.metadata()
            self.app._maybe_auto_download()
            self.assertEqual(check.call_count, 1)
            self.assertEqual(self.app.url_var.get(), url)

    def test_opening_either_panel_closes_the_other_and_keeps_logs(self):
        self.app.append_log("first event")
        self.assertTrue(self.app._log_visible)
        self.app.toggle_settings(True)
        self.assertTrue(self.app.settings_visible)
        self.assertFalse(self.app._log_visible)
        self.assertFalse(self.app.terminal_button._active)
        self.app.toggle_log()
        self.assertFalse(self.app.settings_visible)
        self.assertTrue(self.app._log_visible)
        self.assertFalse(self.app.settings_button._active)
        self.app.toggle_log()
        self.app.clear_log()
        self.app.append_log("new batch, still hidden")
        self.assertFalse(self.app._log_visible)
        self.assertIn("new batch", self.app.log.get("1.0", "end"))

    def test_new_activity_does_not_replace_open_options(self):
        self.app.toggle_settings(True)
        self.app.append_log("first activity")
        self.assertTrue(self.app.settings_visible)
        self.assertFalse(self.app._log_visible)
        self.app.toggle_log(True)
        self.assertIn("first activity", self.app.log.get("1.0", "end"))
        self.assertFalse(self.app.settings_visible)

    def test_mapped_startup_size_settles_before_update_result(self):
        self.tk.deiconify()
        self.assertTrue(pump_until(self.tk, lambda: self.app._layout_id is None))
        self.tk.update_idletasks()
        initial = self.tk.winfo_width(), self.tk.winfo_height()
        self.app._on_update_result({"status": "current"})
        self.tk.update_idletasks()
        self.assertEqual((self.tk.winfo_width(), self.tk.winfo_height()), initial)

    def test_options_fit_at_large_text_without_a_page_scrollbar(self):
        self.app.apply_font_scale(125)
        self.app.queue_list.insert("end", "https://youtu.be/abcdefghijk")
        self.app._refresh_queue_ui()
        self.app.toggle_settings(True)
        self.app._set_running(True)
        self.app._set_progress_visible(True)
        self.app._autosize()
        self.assertFalse(hasattr(self.app, "page_scroll"))
        self.assertLessEqual(self.app.shell.winfo_reqheight() + 6,
                             self.tk.winfo_screenheight() - 64)

    def test_options_compact_spacing_on_short_screen_and_restore_after_close(self):
        with mock.patch.object(self.tk, "winfo_screenheight", return_value=1200):
            self.app.apply_font_scale(125)
            self.app.toggle_settings(True)
            self.app._set_running(True)
            self.app._set_progress_visible(True)
            self.app._autosize()
            normal_height = self.app.shell.winfo_reqheight() + 6
            with mock.patch.object(self.tk, "winfo_screenheight",
                                   return_value=normal_height + 64 - 60):
                self.app._autosize()
                self.assertTrue(self.app._tight_layout)
                self.assertLessEqual(self.app.shell.winfo_reqheight() + 6,
                                     normal_height - 60)
                self.assertFalse(self.app._log_visible)
                self.assertEqual(self.app.font_scale, 125)
                self.app.toggle_settings(False)
                self.app._autosize()
                self.assertFalse(getattr(self.app, "_tight_layout", False))

    def test_link_hint_is_overlay_and_never_becomes_download_input(self):
        self.assertEqual(self.app.url_var.get(), "")
        self.app._sync_placeholder()
        self.assertEqual(self.app.input_canvas.itemcget(self.app._placeholder_window, "state"), "normal")
        self.app.url_var.set("not a link")
        self.assertEqual(self.app.input_canvas.itemcget(self.app._placeholder_window, "state"), "hidden")
        self.assertEqual(self.app._link_status_color, "danger")
        self.assertEqual(self.app.input_canvas.itemcget("indicator", "fill"), self.app.palette["danger"])
        self.app.url_var.set("")
        self.assertEqual(self.app.input_canvas.itemcget(self.app._placeholder_window, "state"), "normal")
        self.assertEqual(self.app.quality_feedback.winfo_manager(), "")

    def test_font_size_updates_controls_without_losing_state_and_is_saved(self):
        self.app.url_var.set("https://youtu.be/abcdefghijk")
        self.app._on_quality(720)
        original = self.app.single_button._font.actual("size")
        self.app.apply_font_scale(125)
        self.assertGreater(self.app.single_button._font.actual("size"), original)
        self.assertEqual(self.app._selected_height(), 720)
        self.assertEqual(self.app.url_var.get(), "https://youtu.be/abcdefghijk")
        self.assertEqual(json.loads(Path(self.config_path).read_text())["font_scale"], 125)
        self.app.apply_font_scale(100)
        self.assertEqual(self.app.single_button._font.actual("size"), original)

    def test_open_failure_does_not_block_ui_or_subsequent_browser_action(self):
        gate = threading.Event()
        def fail(_path):
            gate.wait(3)
            raise OSError("simulated access denied")
        with mock.patch("ytduo.gui.sys.platform", "win32"), \
             mock.patch("ytduo.gui.desktop.open_folder_windows", side_effect=fail), \
             mock.patch("ytduo.gui.desktop.open_url") as browse, \
             mock.patch("ytduo.gui.messagebox.showerror") as show:
            self.app.open_output()
            self.assertIn("folder", self.app._external_pending)
            self.app.star_button.command()
            self.assertTrue(pump_until(self.tk, lambda: "url" not in self.app._external_pending))
            browse.assert_called_once_with(updates.REPO_URL)
            self.assertIn("folder", self.app._external_pending)
            gate.set()
            self.assertTrue(pump_until(self.tk, lambda: not self.app._external_pending))
            show.assert_called_once()
            self.app.star_button.command()
            self.assertTrue(pump_until(self.tk, lambda: not self.app._external_pending))
            self.assertEqual(browse.call_count, 2)
        self.assertTrue(self.app.single_button._enabled)

    def test_new_preferences_are_persisted(self):
        self.app.topmost_var.set(True)
        self.app.open_after_var.set(True)
        self.app._toggle_topmost()
        stored = json.loads(Path(self.config_path).read_text())
        self.assertTrue(stored["always_on_top"])
        self.assertTrue(stored["open_after_download"])

    def metadata(self):
        self.app._on_preview_result({"generation": self.app._preview_generation, "info": INFO})

    def test_music_toggle_keeps_video_default_and_exposes_audio_settings(self):
        self.assertEqual(self.app._make_settings().media_type, "video")
        self.app._on_media("audio")
        self.assertEqual(self.app._make_settings().media_type, "audio")
        self.assertEqual(self.app.quality_row.winfo_manager(), "")
        self.assertEqual(self.app.format_row.winfo_manager(), "")
        self.assertEqual(self.app.music_row.winfo_manager(), "pack")
        self.app._on_audio_format("mp3")
        self.assertEqual(self.app._make_settings().audio_format, "mp3")

    def test_music_hides_video_controls_and_restores_selections(self):
        self.app._on_quality(720)
        self.app._on_mode("compat")
        self.app._on_media("audio")
        self.app._on_audio_quality(192)
        settings = self.app._make_settings()
        self.assertEqual(settings.audio_format, "mp3")
        self.assertEqual(settings.audio_bitrate, 192)
        self.assertEqual(self.app._selected_height(), 720)
        self.assertEqual(self.app.mode_var.get(), "compat")
        self.assertEqual(self.app.quality_row.winfo_manager(), "")
        self.assertEqual(self.app.format_row.winfo_manager(), "")
        self.assertEqual([c.text for c in self.app.chips],
                         ["Best", "2160p", "1440p", "1080p", "720p", "480p", "360p"])
        self.assertEqual([c.text for c in self.app.mode_chips],
                         ["Best quality", "Smaller file", "Most compatible"])
        self.app._on_media("video")
        self.assertEqual(self.app._make_settings().mode, "compat")
        self.assertEqual(self.app.quality_row.winfo_manager(), "pack")
        self.assertEqual(self.app.format_row.winfo_manager(), "pack")

    def test_switching_back_to_original_resets_conversion_bitrate_only(self):
        self.app._on_media("audio")
        self.app._on_audio_quality(320)
        self.app._on_audio_format("original")
        self.assertIsNone(self.app._make_settings().audio_bitrate)
        self.assertEqual(self.app._make_settings().audio_format, "original")

    def test_paste_feedback_and_unavailable_manual_quality(self):
        self.metadata()
        available = {c.value: c._available for c in self.app.chips}
        self.assertTrue(available[1080])
        self.assertTrue(available[720])
        self.assertFalse(available[2160])
        self.app._on_quality(2160)
        self.assertIsNone(available[None])
        self.assertEqual(self.app.quality_feedback.cget("text"), "Ready to download")
        self.assertEqual(self.app.quality_feedback.cget("fg"), self.app.palette["success"])
        self.assertIs(self.app.quality_feedback.master, self.app.input_row)

    def test_editing_url_discards_stale_metadata(self):
        generation = self.app._preview_generation
        self.app.url_var.set("https://www.youtube.com/watch?v=new")
        self.app._on_preview_result({"generation": generation, "info": INFO})
        self.assertIsNone(self.app._preview_info)
        self.assertTrue(all(c._available is None for c in self.app.chips))

    def test_music_metadata_keeps_status_compact(self):
        self.app._on_media("audio")
        self.metadata()
        self.assertEqual(self.app.quality_feedback.cget("text"), "Ready to download")
        self.app._on_audio_format("mp3")
        self.assertEqual(self.app.quality_feedback.cget("text"), "Ready to download")

    def test_link_states_are_compact_and_use_expected_colors(self):
        self.app.url_var.set("not a link")
        self.assertEqual(self.app.quality_feedback.cget("text"), "Invalid link")
        self.assertEqual(self.app.quality_feedback.cget("fg"), self.app.palette["danger"])
        self.app.url_var.set("https://www.youtube.com/watch?v=example")
        self.assertEqual(self.app.quality_feedback.cget("text"), "Scanning…")
        self.assertEqual(self.app.quality_feedback.cget("fg"), self.app.palette["text_dim"])
        self.metadata()
        self.assertEqual(self.app.quality_feedback.cget("text"), "Ready to download")
        self.app.toggle_theme()
        self.assertEqual(self.app.quality_feedback.cget("fg"), self.app.palette["success"])
        self.app.url_var.set("")
        self.assertEqual(self.app.quality_feedback.cget("text"), "Paste a link")
        self.assertTrue(all(c._available is None for c in self.app.chips))

    def test_network_failure_is_not_labelled_an_invalid_link(self):
        self.app._on_preview_result({"generation": self.app._preview_generation,
                                     "error": "connection timed out"})
        self.assertEqual(self.app.quality_feedback.cget("text"), "Could not verify")
        self.assertEqual(self.app.quality_feedback.cget("fg"), self.app.palette["text_dim"])

    def test_preview_keeps_only_latest_pending_request(self):
        gate = threading.Event()
        seen = []

        def probe(url, settings):
            seen.append(url)
            gate.wait(2)
            return dict(INFO, id=url)

        with mock.patch.object(engine, "probe", side_effect=probe), \
             mock.patch.object(engine, "is_host_supported", return_value=True):
            for suffix in ("first", "second", "third"):
                self.app.url_var.set("https://example.com/" + suffix)
                self.app._queue_preview(self.app._preview_generation,
                    self.app.url_var.get(), self.app._make_settings())
            gate.set()
            self.assertTrue(pump_until(self.tk, lambda: self.app._preview_thread is None,
                                       timeout=6))
        self.assertEqual(seen, ["https://example.com/first", "https://example.com/third"])
        self.assertEqual(self.app._preview_info["id"], "https://example.com/third")

    def test_save_controls_only_on_main_screen_and_open_creates_directory(self):
        self.assertIs(self.app.dest_browse.master, self.app.destination_row)
        self.assertFalse(hasattr(self.app, "output_entry"))
        path = str(Path(self.root) / "not-created-yet")
        self.app.output_var.set(path)
        with mock.patch("ytduo.gui.sys.platform", "win32"), \
             mock.patch("ytduo.gui.desktop.open_folder_windows") as start:
            self.app.open_output()
            self.assertTrue(pump_until(self.tk, lambda: not self.app._external_pending))
        self.assertTrue(Path(path).is_dir())
        start.assert_called_once_with(path)

    def test_folder_open_errors_are_visible(self):
        with mock.patch("ytduo.gui.sys.platform", "win32"), \
             mock.patch("ytduo.gui.desktop.open_folder_windows", side_effect=OSError("denied")), \
             mock.patch("ytduo.gui.messagebox.showerror") as show:
            self.app.open_output()
            self.assertTrue(pump_until(self.tk, lambda: not self.app._external_pending))
        show.assert_called_once()

    def test_folder_open_does_not_depend_on_download_settings(self):
        self.app.output_var.set(str(Path(self.root) / "folder with spaces"))
        with mock.patch("ytduo.gui.sys.platform", "win32"), \
             mock.patch.object(self.app, "_make_settings", side_effect=ValueError("bad bitrate")), \
             mock.patch("ytduo.gui.desktop.open_folder_windows") as launch:
            self.app.open_output()
            self.assertTrue(pump_until(self.tk, lambda: not self.app._external_pending))
        launch.assert_called_once_with(self.app.output_var.get())

    def test_theme_switch_cancels_previous_palette_hover_animation(self):
        from ytduo import theme
        self.app.single_button.animate_to(theme.CONTRAST["accent"], steps=8)
        self.assertIsNotNone(self.app.single_button._anim_id)
        self.app.apply_theme("green")
        self.assertIsNone(self.app.single_button._anim_id)
        self.assertEqual(self.app.single_button._current_color, theme.GREEN["accent"])

    def test_all_eight_themes_apply_and_preserve_download_controls(self):
        from ytduo import theme
        for name, palette in theme.PALETTES.items():
            self.app.select_theme(name)
            self.assertEqual(self.app.theme_name, name)
            self.assertEqual(self.app.url_entry.cget("bg"), palette["surface_alt"])
            self.assertEqual(self.app.single_button.palette, palette)
        self.assertEqual(len(theme.PALETTES), 8)

    def test_unix_folder_open_uses_argument_list(self):
        with mock.patch("ytduo.gui.sys.platform", "darwin"), \
             mock.patch("ytduo.gui.subprocess.run", return_value=mock.Mock(returncode=0)) as run:
            self.app.open_output()
            self.assertTrue(pump_until(self.tk, lambda: not self.app._external_pending))
        self.assertEqual(run.call_args.args[0], ["open", self.app._make_settings().output_dir])

    def test_repo_star_opens_browser_only(self):
        with mock.patch("ytduo.gui.desktop.open_url") as browse:
            self.app.star_button.command()
            self.assertTrue(pump_until(self.tk, lambda: not self.app._external_pending))
        browse.assert_called_once_with(updates.REPO_URL)

    def test_update_notice_clears_after_failed_check(self):
        self.app._on_update_result({"status": "available", "latest": "5.0.0"})
        self.assertEqual(self.app.release_button.winfo_manager(), "pack")
        with mock.patch("ytduo.gui.desktop.open_url") as browse:
            self.app.release_button.command()
            self.assertTrue(pump_until(self.tk, lambda: not self.app._external_pending))
        browse.assert_called_once_with(updates.RELEASE_URL)
        self.app._on_update_result({"status": "unavailable"})
        self.assertEqual(self.app.release_button.winfo_manager(), "pack")
        self.assertIn("unavailable", self.app.update_status.cget("text"))

    def test_every_application_launch_requests_a_fresh_update_check(self):
        import tkinter as tk
        from ytduo.gui import YtDuoApp
        for _ in range(2):
            root = tk.Tk()
            root.withdraw()
            app = None
            try:
                with mock.patch.object(YtDuoApp, "_config_path",
                                       return_value=os.path.join(self.root, "startup.json")), \
                     mock.patch.object(YtDuoApp, "check_updates") as check:
                    app = YtDuoApp(root, background_checks=True)
                    self.assertTrue(pump_until(root, lambda: check.called, timeout=3))
                    check.assert_called_once_with(True)
            finally:
                if app:
                    app.destroy()
                root.destroy()

    def test_update_worker_hands_result_to_tk(self):
        with mock.patch.object(self.app.update_checker, "check", return_value={"status": "current"}):
            self.app.check_updates()
            self.assertTrue(pump_until(self.tk, lambda: self.app._update_result is not None))
        self.assertIn("Up to date", self.app.update_status.cget("text"))
