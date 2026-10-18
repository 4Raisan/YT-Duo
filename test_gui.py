"""GUI tests.

These drive the real ``DownyApp`` against the real engine. Network and
ffmpeg are stubbed at the engine boundary, so the event flow, threading,
progress rendering, cancellation and error handling are all exercised
genuinely -- only the download itself is faked.

Skips automatically when tkinter cannot open a display.

Run:  python -m unittest test_gui -v
"""

import os
import shutil
import time
import unittest
import uuid
from unittest import mock

try:
    import tkinter as tk
    HAVE_TK = True
except ImportError:  # pragma: no cover
    HAVE_TK = False

from downy import config, engine
from downy.config import Settings


HERE = os.path.dirname(os.path.abspath(__file__))


def make_workspace():
    path = os.path.join(HERE, f"_guitest-{uuid.uuid4().hex[:10]}")
    os.mkdir(path)
    return path


def _finished(app):
    """True once the app reports a terminal state.

    Wording-agnostic, so cosmetic copy changes to the status line do not
    break behavioural tests.
    """
    return app.status_var.get().startswith(
        ("Done:", "Finished:", "Cancelled", "Stopped"))


def pump_until(root, predicate, timeout=10.0):
    """Run the Tk event loop until ``predicate`` is true or we time out."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        root.update()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def fake_report(tmpdir, name="video.mp4"):
    path = os.path.join(tmpdir, name)
    with open(path, "wb") as fh:
        fh.write(b"x")
    return {
        "path": path, "resolution": "1280x720", "fps": 30.0,
        "video_codec": "h264", "audio_codec": "aac",
        "audio_streams": 1, "duration": 10.0,
    }


@unittest.skipUnless(HAVE_TK, "tkinter unavailable")
class GuiTestCase(unittest.TestCase):
    def setUp(self):
        self.root = make_workspace()
        try:
            self.tk = tk.Tk()
            self.tk.withdraw()
        except tk.TclError as exc:  # pragma: no cover
            self.skipTest(f"no display: {exc}")

        from downy.gui import DownyApp
        self.app = DownyApp(self.tk)
        # Isolate every run inside the temp workspace.
        self.app.output_var.set(self.root)
        self.app.saved = {}

        # ffmpeg present so preflight passes.
        self.ff_patch = mock.patch.object(
            engine, "find_ffmpeg", return_value="ffmpeg")
        self.probe_patch = mock.patch.object(
            engine, "find_ffprobe", return_value="ffprobe")
        self.ff_patch.start()
        self.probe_patch.start()

    def tearDown(self):
        self.ff_patch.stop()
        self.probe_patch.stop()
        try:
            self.app.destroy()
        except Exception:  # noqa: BLE001 - teardown must not mask failures
            pass
        try:
            self.tk.destroy()
        except tk.TclError:
            pass
        shutil.rmtree(self.root, ignore_errors=True)


class CompactLayout(GuiTestCase):
    """The window should stay quiet until it has something to show."""

    def test_options_panel_starts_collapsed(self):
        self.assertFalse(self.app.settings_visible)
        self.assertEqual(self.app.advanced.winfo_manager(), "")

    def test_toggle_shows_and_hides_options(self):
        self.app.toggle_settings()
        self.assertTrue(self.app.settings_visible)
        self.assertEqual(self.app.advanced.winfo_manager(), "pack")
        self.app.toggle_settings()
        self.assertFalse(self.app.settings_visible)
        self.assertEqual(self.app.advanced.winfo_manager(), "")

    def test_log_hidden_until_content_arrives(self):
        self.assertFalse(self.app._log_visible)
        self.app.append_log("hello")
        self.assertTrue(self.app._log_visible)

    def test_start_button_hidden_when_queue_empty(self):
        self.assertEqual(self.app.start_button.winfo_manager(), "")
        self.app.queue_list.insert("end", "https://a.com/1")
        self.app._refresh_queue_ui()
        self.assertEqual(self.app.start_button.winfo_manager(), "pack")

    def test_cancel_button_hidden_when_idle(self):
        self.assertEqual(self.app.cancel_button.winfo_manager(), "")
        self.app._set_running(True)
        self.assertNotEqual(self.app.cancel_button.winfo_manager(), "")
        self.app._set_running(False)
        self.assertEqual(self.app.cancel_button.winfo_manager(), "")

    def test_detail_line_appears_only_with_content(self):
        self.assertEqual(self.app.detail_label.winfo_manager(), "")
        self.app._on_progress({"index": 1, "label": "Video",
                               "percent": " 50.0%", "speed": "1MB/s", "eta": ""})
        self.assertEqual(self.app.detail_label.winfo_manager(), "pack")

    def test_adding_to_queue_reveals_the_queue(self):
        self.app.url_var.set("https://youtu.be/x")
        self.app.add_to_queue()
        self.assertTrue(self.app.settings_visible)
        self.assertEqual(self.app.queue_count.cget("text"), "1 link(s) queued")

    def test_multiple_links_route_to_the_queue_instead_of_erroring(self):
        self.app.url_var.set("https://a.com/1 https://a.com/2")
        with mock.patch.object(engine, "run_item", return_value={"path": "x"}), \
             mock.patch("downy.gui.messagebox.showinfo") as info:
            self.app.download_single()
            pump_until(self.tk, lambda: self.app.worker is None)
        self.assertFalse(info.called, "should queue, not warn")
        self.assertIn("2 link(s) queued", self.app.queue_count.cget("text"))

    def test_window_is_compact(self):
        self.assertLessEqual(self.tk.winfo_width(), 760)
        self.assertLessEqual(self.tk.winfo_height(), 520)


class ModePicker(GuiTestCase):
    """The Format dropdown shows friendly labels but stores real modes."""

    def test_selecting_a_label_sets_the_mode(self):
        from downy.gui import MODE_LABELS, LABEL_TO_MODE
        index = MODE_LABELS.index("Most compatible")
        self.app.mode_combo.current(index)
        self.app.mode_combo.event_generate("<<ComboboxSelected>>")
        self.tk.update()
        self.assertEqual(self.app.mode_var.get(), LABEL_TO_MODE["Most compatible"])
        self.assertEqual(self.app.mode_var.get(), "compat")

    def test_setting_mode_var_updates_the_label(self):
        self.app.mode_var.set("efficient")
        self.assertEqual(self.app._mode_label_var.get(), "Smaller file")

    def test_saved_mode_restores_the_label(self):
        """A saved config drives both the mode var and its friendly label."""
        from downy import gui

        original = gui.read_config_file
        gui.read_config_file = lambda _path: {"mode": "compat"}
        try:
            other = gui.DownyApp(tk.Toplevel(self.tk))
        finally:
            gui.read_config_file = original
        try:
            self.assertEqual(other.mode_var.get(), "compat")
            self.assertEqual(other._mode_label_var.get(), "Most compatible")
        finally:
            other.destroy()

    def test_help_text_follows_the_mode(self):
        self.app.mode_var.set("compat")
        self.assertIn("H.264", self.app.mode_help.cget("text"))


class WindowConstruction(GuiTestCase):
    def test_defaults(self):
        self.assertEqual(self.app.quality_combo.get(), "Best available")
        self.assertEqual(self.app.mode_var.get(), "best")
        self.assertEqual(self.app.status_var.get(), "Ready.")

    def test_quality_choices_map_to_heights(self):
        from downy.gui import QUALITY_CHOICES
        self.assertIsNone(QUALITY_CHOICES[0][1])
        self.assertIn(720, [h for _, h in QUALITY_CHOICES])

    def test_selected_quality_reaches_settings(self):
        from downy.gui import QUALITY_CHOICES
        index = next(i for i, (_, h) in enumerate(QUALITY_CHOICES) if h == 720)
        self.app.quality_combo.current(index)
        self.assertEqual(self.app._make_settings().max_height, 720)

    def test_settings_reflect_widgets(self):
        self.app.mode_var.set("compat")
        self.app.cookies_var.set("chrome")
        settings = self.app._make_settings()
        self.assertEqual(settings.mode, "compat")
        self.assertEqual(settings.cookies_from_browser, "chrome")
        self.assertEqual(settings.output_dir, self.root)

    def test_mode_help_updates(self):
        self.app.mode_var.set("compat")
        self.app._update_mode_help()
        self.assertIn("H.264", self.app.mode_help.cget("text"))


class QueueControls(GuiTestCase):
    def test_add_from_link_bar(self):
        self.app.url_var.set("https://youtube.com/watch?v=abc")
        self.app.add_to_queue()
        self.assertEqual(self.app.queue_items(), ["https://youtube.com/watch?v=abc"])
        self.assertEqual(self.app.url_var.get(), "")

    def test_add_multiple_urls_in_one_line(self):
        self.app.url_var.set("https://a.com/1 and https://a.com/2")
        self.app.add_to_queue()
        self.assertEqual(len(self.app.queue_items()), 2)

    def test_remove_and_clear(self):
        for n in range(3):
            self.app.queue_list.insert("end", f"https://a.com/{n}")
        self.app.queue_list.selection_set(1)
        self.app.remove_selected()
        self.assertEqual(len(self.app.queue_items()), 2)
        self.app.clear_queue()
        self.assertEqual(self.app.queue_items(), [])

    def test_ignores_text_without_urls(self):
        self.app.url_var.set("just some notes")
        self.app.add_to_queue()
        self.assertEqual(self.app.queue_items(), [])


class DownloadFlow(GuiTestCase):
    def test_single_download_success_flow(self):
        report = fake_report(self.root)

        def fake_run_item(url, index, total, settings, emit, should_stop=None):
            emit("item_start", {"index": index, "total": total, "url": url})
            emit("probe", {"index": index, "title": "T",
                           "video": "137 h264 1280x720", "audio": "140 aac"})
            emit("progress", {"index": index, "label": "Video",
                              "percent": " 42.0%", "speed": "1.0MiB/s",
                              "eta": "00:03"})
            emit("stream_done", {"index": index, "label": "Video", "size": "10.0 MB"})
            emit("merged", {"index": index, "report": report})
            return report

        with mock.patch.object(engine, "run_item", side_effect=fake_run_item):
            self.app.url_var.set("https://youtube.com/watch?v=abc")
            self.app.download_single()
            done = pump_until(
                self.tk, lambda: self.app.worker is None and
                _finished(self.app))

        self.assertTrue(done, f"status stuck at {self.app.status_var.get()!r}")
        self.assertIn("1 downloaded", self.app.status_var.get())
        self.assertNotIn("failed", self.app.status_var.get())
        self.assertEqual(self.app.progress.cget("value"), 100)

        log = self.app.log.get("1.0", "end")
        self.assertIn("T", log)
        self.assertIn("1280x720", log)
        self.assertIn("saved:", log)
        self.assertEqual(self.app.url_var.get(), "")

    def test_queue_download_processes_every_item(self):
        report = fake_report(self.root)
        seen = []

        def fake_run_item(url, index, total, settings, emit, should_stop=None):
            seen.append(url)
            return report

        with mock.patch.object(engine, "run_item", side_effect=fake_run_item):
            for n in range(3):
                self.app.queue_list.insert("end", f"https://a.com/{n}")
            self.app.start_queue()
            done = pump_until(
                self.tk, lambda: self.app.worker is None and
                _finished(self.app))

        self.assertTrue(done)
        self.assertEqual(len(seen), 3)
        self.assertIn("3 downloaded", self.app.status_var.get())

    def test_progress_bar_follows_events(self):
        self.app._on_progress({"index": 1, "label": "Video",
                               "percent": " 67.5%", "speed": "", "eta": ""})
        self.assertEqual(self.app.progress.cget("value"), 67.5)
        self.assertIn("67.5%", self.app.detail_var.get())

    def test_progress_with_garbage_percent_does_not_raise(self):
        self.app._on_progress({"index": 1, "label": "Video",
                               "percent": "n/a", "speed": "", "eta": ""})
        self.assertEqual(self.app.progress.cget("value"), 0)


class FailureHandling(GuiTestCase):
    def test_failed_item_is_reported_not_raised(self):
        def fake_run_item(url, index, total, settings, emit, should_stop=None):
            emit("item_failed", {"index": index, "reason": "video unavailable",
                                 "detail": "ERROR: Video unavailable"})
            raise ValueError("ERROR: Video unavailable")

        with mock.patch.object(engine, "run_item", side_effect=fake_run_item):
            self.app.url_var.set("https://youtube.com/watch?v=bad")
            self.app.download_single()
            done = pump_until(
                self.tk, lambda: self.app.worker is None and
                _finished(self.app))

        self.assertTrue(done)
        self.assertIn("1 failed", self.app.status_var.get())
        self.assertIn("video unavailable", self.app.log.get("1.0", "end"))

    def test_worker_error_shows_dialog(self):
        with mock.patch.object(engine, "run_batch",
                               side_effect=RuntimeError("boom")), \
             mock.patch("downy.gui.messagebox.showerror") as error_box:
            self.app.url_var.set("https://youtube.com/watch?v=abc")
            self.app.download_single()
            done = pump_until(self.tk, lambda: self.app.worker is None)

        self.assertTrue(done)
        self.assertTrue(error_box.called)
        self.assertIn("Stopped", self.app.status_var.get())

    def test_unsupported_host_fails_fast_in_gui(self):
        with mock.patch.object(engine, "is_host_supported", return_value=False):
            self.app.url_var.set("https://cinesubz.lk/x/")
            self.app.download_single()
            done = pump_until(
                self.tk, lambda: self.app.worker is None and
                _finished(self.app))

        self.assertTrue(done)
        self.assertIn("1 failed", self.app.status_var.get())
        self.assertIn("host not supported",
                      self.app.log.get("1.0", "end"))

    def test_missing_ffmpeg_blocks_start(self):
        with mock.patch.object(engine, "find_ffmpeg", return_value=None), \
             mock.patch("downy.gui.messagebox.showerror") as error_box:
            self.app.url_var.set("https://youtube.com/watch?v=abc")
            self.app.download_single()

        self.assertTrue(error_box.called)
        self.assertIsNone(self.app.worker)

    def test_empty_input_does_not_start(self):
        with mock.patch("downy.gui.messagebox.showinfo") as info:
            self.app.download_single()
        self.assertTrue(info.called)
        self.assertIsNone(self.app.worker)


class Cancellation(GuiTestCase):
    def test_cancel_interrupts_an_in_flight_download(self):
        """Cancel must interrupt the current download, not just the queue."""
        started = []

        def slow_run_item(url, index, total, settings, emit, should_stop=None):
            started.append(url)
            # A real download polls should_stop between chunks.
            for _ in range(600):
                if should_stop and should_stop():
                    raise engine.Cancelled()
                time.sleep(0.005)
            return fake_report(self.root, f"{index}.mp4")

        with mock.patch.object(engine, "run_item", side_effect=slow_run_item):
            for n in range(5):
                self.app.queue_list.insert("end", f"https://a.com/{n}")
            self.app.start_queue()
            self.assertTrue(pump_until(
                self.tk, lambda: len(started) >= 1, timeout=5))
            self.app.cancel()
            stopped = pump_until(
                self.tk, lambda: self.app.worker is None, timeout=20)

        self.assertTrue(stopped, "worker did not stop after cancel")
        self.assertIn("Cancelled", self.app.status_var.get())
        self.assertEqual(len(started), 1,
                         "cancel should stop the first item, not continue the queue")

    def test_cancel_between_items_stops_the_queue(self):
        started = []
        stop = {"v": False}

        def fake_run_item(url, index, total, settings, emit, should_stop=None):
            started.append(url)
            stop["v"] = True          # user clicks Cancel right after item 1
            return fake_report(self.root, f"{index}.mp4")

        with mock.patch.object(engine, "run_item", side_effect=fake_run_item):
            for n in range(5):
                self.app.queue_list.insert("end", f"https://a.com/{n}")
            self.app.start_queue()
            # The app's own should_stop is the bridge; drive it directly.
            self.app.bridge.request_cancel()
            done = pump_until(self.tk, lambda: self.app.worker is None, timeout=15)

        self.assertTrue(done)
        self.assertIn("Cancelled", self.app.status_var.get())

    def test_cancel_button_state_tracks_running(self):
        self.app._set_running(True)
        self.assertEqual(str(self.app.cancel_button.cget("state")), "normal")
        self.assertEqual(str(self.app.start_button.cget("state")), "disabled")
        self.app._set_running(False)
        self.assertEqual(str(self.app.cancel_button.cget("state")), "disabled")
        self.assertEqual(str(self.app.start_button.cget("state")), "normal")

    def test_cancel_without_running_is_harmless(self):
        self.app.cancel()  # must not raise
        self.assertEqual(self.app.status_var.get(), "Ready.")


class EventBridgeBehaviour(unittest.TestCase):
    def test_pump_delivers_in_order(self):
        from downy.gui_worker import EventBridge
        bridge = EventBridge()
        bridge.emit("a", {"n": 1})
        bridge.emit("b", {"n": 2})
        seen = []
        bridge.pump(lambda name, data: seen.append((name, data["n"])))
        self.assertEqual(seen, [("a", 1), ("b", 2)])

    def test_pump_survives_a_failing_handler(self):
        from downy.gui_worker import EventBridge
        bridge = EventBridge()
        bridge.emit("a", {})
        bridge.emit("b", {})
        seen = []

        def handler(name, data):
            seen.append(name)
            raise RuntimeError("handler bug")

        bridge.pump(handler)
        self.assertEqual(seen, ["a", "b"])

    def test_reset_clears_cancel_and_queue(self):
        from downy.gui_worker import EventBridge
        bridge = EventBridge()
        bridge.request_cancel()
        bridge.emit("a", {})
        bridge.reset()
        self.assertFalse(bridge.cancelled)
        seen = []
        bridge.pump(lambda n, d: seen.append(n))
        self.assertEqual(seen, [])

    def test_worker_reports_cancelled(self):
        from downy.gui_worker import Cancelled, EventBridge, Worker
        bridge = EventBridge()
        worker = Worker(lambda: (_ for _ in ()).throw(Cancelled()), bridge)
        worker.start()
        worker.join(timeout=5)
        seen = []
        bridge.pump(lambda n, d: seen.append(n))
        self.assertEqual(seen, ["worker_cancelled"])


class SettingsPersistence(GuiTestCase):
    def test_saved_settings_round_trip(self):
        path = os.path.join(self.root, "config.json")
        with mock.patch.object(self.app, "_config_path", return_value=path):
            self.app.mode_var.set("compat")
            self.app.cookies_var.set("firefox")
            self.app._save_settings()
            saved = config.read_config_file(path)

        self.assertEqual(saved["mode"], "compat")
        self.assertEqual(saved["cookies_from_browser"], "firefox")
        self.assertEqual(saved["output_dir"], self.root)

    def test_corrupt_config_still_loads_defaults(self):
        path = os.path.join(self.root, "config.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{broken")
        self.assertEqual(config.read_config_file(path), {})


class UrlExtraction(unittest.TestCase):
    def test_strips_trailing_punctuation(self):
        from downy.cli import extract_urls
        self.assertEqual(extract_urls("See https://youtu.be/AAA."),
                         ["https://youtu.be/AAA"])
        self.assertEqual(extract_urls("(https://youtu.be/AAA)"),
                         ["https://youtu.be/AAA"])

    def test_ignores_non_urls(self):
        from downy.cli import extract_urls
        self.assertEqual(extract_urls("just notes, no links"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)




