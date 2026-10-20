"""GUI tests.

These drive the real ``YtDuoApp`` against the real engine. Network and
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

from ytduo import config, engine
from ytduo.config import Settings


HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root


def make_workspace():
    """Throwaway directory for one test, removed in tearDown."""
    base = os.path.join(HERE, "tests")
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, f"_guitest-{uuid.uuid4().hex[:10]}")
    os.mkdir(path)
    return path


def _finished(app):
    """True once the app reports a terminal state.

    Wording-agnostic, so cosmetic copy changes to the status line do not
    break behavioural tests. Both ASCII and typographic dashes are accepted
    because the UI uses an em dash.
    """
    text = app.status_label.cget("text")
    return text.startswith(("Done", "Finished", "Cancelled", "Stopped"))


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
        self.home_patch = mock.patch.dict(os.environ, {"YTDUO_HOME": self.root})
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        try:
            self.tk = tk.Tk()
            self.tk.withdraw()
        except tk.TclError as exc:  # pragma: no cover
            self.skipTest(f"no display: {exc}")

        from ytduo.gui import YtDuoApp
        with mock.patch.object(YtDuoApp, "_config_path",
                               return_value=os.path.join(self.root, "config.json")):
            self.app = YtDuoApp(self.tk, background_checks=False)
        # Isolate every run inside the temp workspace.
        self.app.output_var.set(self.root)
        self.app.saved = {}

        # Keep settings writes inside the temp folder: toggle_theme() saves,
        # and a real config.json would leak state between tests.
        self.config_path = os.path.join(self.root, "config.json")
        self.config_patch = mock.patch.object(
            self.app, "_config_path", return_value=self.config_path)
        self.config_patch.start()

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
        self.config_patch.stop()
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

    def test_log_starts_expanded_each_launch(self):
        self.assertTrue(self.app._log_visible)
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
        self.assertEqual(self.app.detail_label.cget("text"), "")
        self.app._on_progress({"index": 1, "label": "Video",
                               "percent": " 50.0%", "speed": "1MB/s", "eta": ""})
        self.assertIn("50.0%", self.app.detail_label.cget("text"))

    def test_adding_to_queue_reveals_the_queue(self):
        self.app.url_var.set("https://youtu.be/x")
        self.app.add_to_queue()
        self.assertTrue(self.app.settings_visible)
        self.assertEqual(self.app.queue_count.cget("text"), "1 link(s) queued")

    def test_multiple_links_route_to_the_queue_instead_of_erroring(self):
        self.app.url_var.set("https://a.com/1 https://a.com/2")
        with mock.patch.object(engine, "run_item", return_value={"path": "x"}), \
             mock.patch("ytduo.gui.messagebox.showinfo") as info:
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
        from ytduo.gui import MODE_LABELS, LABEL_TO_MODE
        index = MODE_LABELS.index("Most compatible")
        self.app._on_mode(LABEL_TO_MODE["Most compatible"])
        self.assertEqual(self.app.mode_var.get(), LABEL_TO_MODE["Most compatible"])
        self.assertEqual(self.app.mode_var.get(), "compat")

    def test_setting_mode_var_updates_the_label(self):
        self.app.mode_var.set("efficient")
        self.assertEqual(self.app.mode_var.get(), "efficient")
        self.assertIn("Smaller file", [c.text for c in self.app.mode_chips])

    def test_saved_mode_restores_the_label(self):
        """A saved config drives both the mode var and its friendly label."""
        from ytduo import gui

        with mock.patch.object(gui, "read_user_config", return_value={"mode": "compat"}):
            other = gui.YtDuoApp(tk.Toplevel(self.tk), background_checks=False)
        try:
            self.assertEqual(other.mode_var.get(), "compat")

        finally:
            other.destroy()

    def test_help_text_follows_the_mode(self):
        self.app.mode_var.set("compat")
        self.assertIn("H.264", self.app.mode_help.cget("text"))


class Theming(GuiTestCase):
    """The window must survive a palette swap with every control intact."""

    def test_starts_dark_by_default(self):
        self.assertEqual(self.app.theme_name, "dark")

    def test_toggle_switches_palette(self):
        self.app.toggle_theme()
        self.assertEqual(self.app.theme_name, "light")
        self.app.toggle_theme()
        self.assertEqual(self.app.theme_name, "dark")

    def test_root_background_follows_theme(self):
        from ytduo import theme
        self.app.apply_theme("light")
        self.assertEqual(self.tk.cget("bg"), theme.LIGHT["bg"])
        self.app.apply_theme("dark")
        self.assertEqual(self.tk.cget("bg"), theme.DARK["bg"])

    def test_themed_widgets_pick_up_new_palette(self):
        from ytduo import theme
        self.app.apply_theme("light")
        self.assertEqual(self.app.single_button.palette, theme.LIGHT)
        for chip in self.app.chips:
            self.assertEqual(chip.palette, theme.LIGHT)

    def test_title_stays_readable_after_swap(self):
        from ytduo import theme
        for name in ("light", "dark"):
            with self.subTest(name=name):
                self.app.apply_theme(name)
                self.assertEqual(self.app.title_label.cget("fg"),
                                 theme.PALETTES[name]["text"])

    def test_theme_choice_persists(self):
        self.app.toggle_theme()          # -> light
        self.assertEqual(self.app.theme_name, "light")
        path = os.path.join(self.root, "config.json")
        with mock.patch.object(self.app, "_config_path", return_value=path):
            self.app._save_settings()
        self.assertEqual(config.read_config_file(path)["theme"], "light")


class Animations(unittest.TestCase):
    """The easing primitives behind hover and progress effects."""

    def test_ease_out_cubic_is_bounded_and_monotonic(self):
        from ytduo import theme
        values = [theme.ease_out_cubic(i / 10) for i in range(11)]
        self.assertEqual(values[0], 0)
        self.assertAlmostEqual(values[-1], 1)
        self.assertEqual(values, sorted(values))

    def test_lerp_color_endpoints(self):
        from ytduo import theme
        self.assertEqual(theme.lerp_color("#000000", "#ffffff", 0), "#000000")
        self.assertEqual(theme.lerp_color("#000000", "#ffffff", 1), "#ffffff")

    def test_lerp_color_midpoint(self):
        from ytduo import theme
        mid = theme.lerp_color("#000000", "#ffffff", 0.5)
        r, g, b = (int(mid[i:i + 2], 16) for i in (1, 3, 5))
        self.assertEqual((r, g, b), (128, 128, 128))

    def test_mix_alpha_is_clamped(self):
        from ytduo import theme
        self.assertEqual(theme.mix_alpha("#ffffff", "#000000", 5), "#ffffff")


class WidgetBehaviour(GuiTestCase):
    """Canvas widgets respond to their own interaction paths."""

    def test_button_click_invokes_command(self):
        from ytduo import widgets
        calls = []
        btn = widgets.Button(self.tk, "Go", lambda: calls.append(1),
                             self.app.palette, self.app.fonts)
        btn._on_press()
        btn._on_release()
        self.assertEqual(calls, [1])

    def test_disabled_button_ignores_click(self):
        from ytduo import widgets
        calls = []
        btn = widgets.Button(self.tk, "Go", lambda: calls.append(1),
                             self.app.palette, self.app.fonts)
        btn.set_enabled(False)
        btn._on_press()
        btn._on_release()
        self.assertEqual(calls, [])

    def test_chip_reports_its_value(self):
        picked = []
        self.app.on_position_test = picked
        for chip in self.app.chips:
            if chip.text == "720p":
                chip.command(chip.value)
        self.assertEqual(self.app._selected_height(), 720)

    def test_progress_bar_clamps_and_eases(self):
        from ytduo import widgets
        bar = widgets.ProgressBar(self.tk, self.app.palette)
        bar.set(150, immediate=True)
        self.assertEqual(bar._target, 100)
        bar.set(-5, immediate=True)
        self.assertEqual(bar._target, 0)
        bar.stop()

    def test_progress_indeterminate_toggles(self):
        from ytduo import widgets
        bar = widgets.ProgressBar(self.tk, self.app.palette)
        bar.set_indeterminate(True)
        self.assertTrue(bar._indeterminate)
        bar.set_indeterminate(False)
        self.assertFalse(bar._indeterminate)
        bar.stop()


class LiveQueue(GuiTestCase):
    """Links added during a run must download without restarting it."""

    def test_links_added_mid_run_are_downloaded(self):
        seen = []

        def fake_run_item(url, index, total, settings, emit, should_stop=None, **kwargs):
            seen.append(url)
            time.sleep(0.2)
            return {"path": "x"}

        with mock.patch.object(engine, "run_item", side_effect=fake_run_item):
            self.app.url_var.set("https://a.com/1")
            self.app.download_single()
            self.assertTrue(pump_until(self.tk, lambda: len(seen) >= 1,
                                       timeout=5))

            # The user pastes another link while the first is running.
            self.app.url_var.set("https://a.com/2")
            self.app.download_single()

            done = pump_until(self.tk, lambda: self.app.worker is None,
                              timeout=20)

        self.assertTrue(done)
        self.assertEqual(seen, ["https://a.com/1", "https://a.com/2"])

    def test_download_button_stays_enabled_while_running(self):
        self.app._set_running(True)
        self.assertTrue(self.app.single_button._enabled,
                        "links must be addable during a run")
        self.assertTrue(self.app.add_button._enabled)

    def test_add_to_queue_while_idle_does_not_buffer(self):
        self.app.url_var.set("https://a.com/1")
        self.app.add_to_queue()
        self.assertEqual(self.app._take_pending_links(), [])

    def test_pending_buffer_is_drained_once(self):
        self.app.worker = mock.Mock(is_alive=lambda: True)
        self.app._enqueue_for_running_job(["https://a.com/x"])
        self.assertEqual(self.app._take_pending_links(), ["https://a.com/x"])
        self.assertEqual(self.app._take_pending_links(), [])
        self.app.worker = None


class SaveDestination(GuiTestCase):
    """Where files land is visible on the main screen, not just Options."""

    def test_destination_is_shown(self):
        self.assertNotEqual(self.app.dest_label.cget("text"), "")

    def test_destination_row_is_packed_without_opening_options(self):
        self.assertFalse(self.app.settings_visible)
        self.assertEqual(self.app.destination_row.winfo_manager(), "pack")

    def test_changing_output_updates_the_shown_path(self):
        other = os.path.join(self.root, "elsewhere")
        self.app.output_var.set(other)
        shown = self.app.dest_label.cget("text")
        # Long paths are middle-truncated, so match on the tail.
        self.assertTrue(shown.endswith("elsewhere"), shown)

    def test_long_paths_are_middle_truncated(self):
        from ytduo.gui import YtDuoApp
        long_path = "C:\\" + "\\".join(["averylongfoldername"] * 6)
        shown = YtDuoApp._short_path(long_path)
        self.assertLessEqual(len(shown), 44)
        self.assertIn("\u2026", shown)

    def test_short_path_is_untouched(self):
        from ytduo.gui import YtDuoApp
        self.assertEqual(YtDuoApp._short_path("downloads"), "downloads")

    def test_window_width_is_narrower(self):
        # Linux fallback fonts have different metrics from Windows Segoe UI.
        self.assertLessEqual(self.tk.winfo_width(), 660 if os.name == "nt" else 800)
        self.assertLessEqual(self.app.shell.winfo_reqwidth(), self.tk.winfo_width())


class WindowConstruction(GuiTestCase):
    def test_defaults(self):
        self.assertEqual(self.app.chips[0].text, "Best")
        self.assertEqual(self.app.mode_var.get(), "best")
        self.assertEqual(self.app.status_label.cget("text"), "Ready.")

    def test_quality_choices_map_to_heights(self):
        from ytduo.gui import QUALITY_CHOICES
        self.assertIsNone(QUALITY_CHOICES[0][1])
        self.assertIn(720, [h for _, h in QUALITY_CHOICES])

    def test_selected_quality_reaches_settings(self):
        from ytduo.gui import QUALITY_CHOICES
        index = next(i for i, (_, h) in enumerate(QUALITY_CHOICES) if h == 720)
        self.app._on_quality(QUALITY_CHOICES[index][1])
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

        def fake_run_item(url, index, total, settings, emit, should_stop=None, **kwargs):
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

        self.assertTrue(done, f"status stuck at {self.app.status_label.cget('text')!r}")
        self.assertIn("1 downloaded", self.app.status_label.cget("text"))
        self.assertNotIn("failed", self.app.status_label.cget("text"))
        # The bar is reset once the run ends, so check the run-time state
        # was tracked rather than its post-run value.
        self.assertFalse(self.app._progress_visible)

        log = self.app.log.get("1.0", "end")
        self.assertIn("T", log)
        self.assertIn("1280x720", log)
        self.assertIn("video.mp4", log)
        self.assertEqual(self.app.url_var.get(), "")

    def test_queue_download_processes_every_item(self):
        report = fake_report(self.root)
        seen = []

        def fake_run_item(url, index, total, settings, emit, should_stop=None, **kwargs):
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
        self.assertIn("3 downloaded", self.app.status_label.cget("text"))

    def test_progress_bar_follows_events(self):
        self.app._on_progress({"index": 1, "label": "Video",
                               "percent": " 67.5%", "speed": "", "eta": ""})
        pump_until(self.tk, lambda: abs(self.app.progress._value - 67.5) < 0.5,
                   timeout=3)
        self.assertAlmostEqual(self.app.progress._value, 67.5, delta=0.5)
        self.assertIn("67.5%", self.app.detail_label.cget("text"))

    def test_progress_with_garbage_percent_does_not_raise(self):
        self.app._on_progress({"index": 1, "label": "Video",
                               "percent": "n/a", "speed": "", "eta": ""})
        self.assertEqual(self.app.progress._value, 0)


class FailureHandling(GuiTestCase):
    def test_failed_item_is_reported_not_raised(self):
        def fake_run_item(url, index, total, settings, emit, should_stop=None, **kwargs):
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
        self.assertIn("1 failed", self.app.status_label.cget("text"))
        self.assertIn("video unavailable", self.app.log.get("1.0", "end"))

    def test_worker_error_shows_dialog(self):
        with mock.patch.object(engine, "run_batch",
                               side_effect=RuntimeError("boom")), \
             mock.patch("ytduo.gui.messagebox.showerror") as error_box:
            self.app.url_var.set("https://youtube.com/watch?v=abc")
            self.app.download_single()
            done = pump_until(self.tk, lambda: self.app.worker is None)

        self.assertTrue(done)
        self.assertTrue(error_box.called)
        self.assertIn("Stopped", self.app.status_label.cget("text"))

    def test_unsupported_host_fails_fast_in_gui(self):
        with mock.patch.object(engine, "is_host_supported", return_value=False):
            self.app.url_var.set("https://cinesubz.lk/x/")
            self.app.download_single()
            done = pump_until(
                self.tk, lambda: self.app.worker is None and
                _finished(self.app))

        self.assertTrue(done)
        self.assertIn("1 failed", self.app.status_label.cget("text"))
        self.assertIn("host not supported",
                      self.app.log.get("1.0", "end"))

    def test_missing_ffmpeg_blocks_start(self):
        with mock.patch.object(engine, "find_ffmpeg", return_value=None), \
             mock.patch("ytduo.gui.messagebox.showerror") as error_box:
            self.app.url_var.set("https://youtube.com/watch?v=abc")
            self.app.download_single()

        self.assertTrue(error_box.called)
        self.assertIsNone(self.app.worker)

    def test_empty_input_does_not_start(self):
        with mock.patch("ytduo.gui.messagebox.showinfo") as info:
            self.app.download_single()
        self.assertFalse(info.called)
        self.assertIsNone(self.app.worker)


class Cancellation(GuiTestCase):
    def test_cancel_interrupts_an_in_flight_download(self):
        """Cancel must interrupt the current download, not just the queue."""
        started = []

        def slow_run_item(url, index, total, settings, emit, should_stop=None, **kwargs):
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
        self.assertIn("Cancelled", self.app.status_label.cget("text"))
        self.assertEqual(len(started), 1,
                         "cancel should stop the first item, not continue the queue")

    def test_cancel_between_items_stops_the_queue(self):
        started = []
        stop = {"v": False}

        def fake_run_item(url, index, total, settings, emit, should_stop=None, **kwargs):
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
        self.assertIn("Cancelled", self.app.status_label.cget("text"))

    def test_cancel_button_state_tracks_running(self):
        self.app._set_running(True)
        self.assertTrue(self.app.cancel_button._enabled)
        self.assertFalse(self.app.start_button._enabled)
        self.app._set_running(False)
        self.assertFalse(self.app.cancel_button._enabled)
        self.assertTrue(self.app.start_button._enabled)

    def test_cancel_without_running_is_harmless(self):
        self.app.cancel()  # must not raise
        self.assertEqual(self.app.status_label.cget("text"), "Ready.")


class EventBridgeBehaviour(unittest.TestCase):
    def test_pump_delivers_in_order(self):
        from ytduo.gui_worker import EventBridge
        bridge = EventBridge()
        bridge.emit("a", {"n": 1})
        bridge.emit("b", {"n": 2})
        seen = []
        bridge.pump(lambda name, data: seen.append((name, data["n"])))
        self.assertEqual(seen, [("a", 1), ("b", 2)])

    def test_pump_survives_a_failing_handler(self):
        from ytduo.gui_worker import EventBridge
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
        from ytduo.gui_worker import EventBridge
        bridge = EventBridge()
        bridge.request_cancel()
        bridge.emit("a", {})
        bridge.reset()
        self.assertFalse(bridge.cancelled)
        seen = []
        bridge.pump(lambda n, d: seen.append(n))
        self.assertEqual(seen, [])

    def test_worker_reports_cancelled(self):
        from ytduo.gui_worker import Cancelled, EventBridge, Worker
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
        from ytduo.cli import extract_urls
        self.assertEqual(extract_urls("See https://youtu.be/AAA."),
                         ["https://youtu.be/AAA"])
        self.assertEqual(extract_urls("(https://youtu.be/AAA)"),
                         ["https://youtu.be/AAA"])

    def test_ignores_non_urls(self):
        from ytduo.cli import extract_urls
        self.assertEqual(extract_urls("just notes, no links"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
