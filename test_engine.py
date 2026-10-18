"""Offline tests for the engine, naming, config, and queue parsing.

Everything here runs without network access, without ffmpeg, and without
touching the real project folders: each test builds its own Settings that
points at a temp directory under the test folder.

Run:  python -m unittest test_engine -v
"""

import json
import os
import shutil
import subprocess
import sys
import time
import unittest
import uuid
from unittest import mock

from downy import config, engine, naming, selector
from downy.config import Settings
from downy.cli import read_queue


HERE = os.path.dirname(os.path.abspath(__file__))


def make_workspace():
    """Create a throwaway directory under the project folder.

    Deliberately *not* ``tempfile.mkdtemp``: on Windows that applies an
    owner-only ACL to the new directory, which makes it awkward for tooling
    that runs under a different token. A plain mkdir inherits the parent's
    permissions and behaves like any other project subfolder.
    """
    path = os.path.join(HERE, f"_test-{uuid.uuid4().hex[:12]}")
    os.mkdir(path)
    return path


class TempWorkspace(unittest.TestCase):
    """Base class giving each test a throwaway project root."""

    def setUp(self):
        self.root = make_workspace()
        self.settings = Settings(root=self.root)
        self.settings.ensure_dirs()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, path, content=b""):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "wb" if isinstance(content, bytes) else "w"
        kwargs = {} if isinstance(content, bytes) else {"encoding": "utf-8"}
        with open(path, mode, **kwargs) as fh:
            fh.write(content if content else b"" if mode == "wb" else "")
        return path


# ── format_size ──────────────────────────────────────────────────────

class FormatSize(unittest.TestCase):
    def test_tiers(self):
        cases = {
            0: "? MB",
            None: "? MB",
            512: "512 B",
            1024: "1.0 KB",
            1536: "1.5 KB",
            1024**2: "1.0 MB",
            1024**3: "1.0 GB",
            2 * 1024**4: "2.0 TB",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(engine.format_size(value), expected)

    def test_negative_is_unknown(self):
        self.assertEqual(engine.format_size(-5), "? MB")


# ── strip_ansi ───────────────────────────────────────────────────────

class StripAnsi(unittest.TestCase):
    def test_colour_and_cursor_codes(self):
        self.assertEqual(engine.strip_ansi("\x1b[31mred\x1b[0m"), "red")
        self.assertEqual(engine.strip_ansi("line\x1b[2K"), "line")
        self.assertEqual(engine.strip_ansi("x\x1b[G\x1b[?25l"), "x")


# ── find_file ────────────────────────────────────────────────────────

class FindFile(TempWorkspace):
    def test_ignores_partials(self):
        self.write(os.path.join(self.settings.video_dir, "abc_video.webm.part"))
        self.write(os.path.join(self.settings.video_dir, "abc_video.webm.ytdl"))
        self.assertIsNone(engine.find_file(self.settings.video_dir, "abc", "video"))

    def test_prefers_newest_complete_file(self):
        old = self.write(os.path.join(self.settings.video_dir, "abc_video.mp4"))
        new = self.write(os.path.join(self.settings.video_dir, "abc_video.webm"))
        past = time.time() - 500
        os.utime(old, (past, past))
        self.assertEqual(engine.find_file(self.settings.video_dir, "abc", "video"), new)

    def test_returns_none_when_absent(self):
        self.assertIsNone(engine.find_file(self.settings.video_dir, "nope", "video"))


# ── recycle pruning ──────────────────────────────────────────────────

class RecyclePruning(TempWorkspace):
    def test_removes_files_older_than_the_cap(self):
        old = self.write(os.path.join(self.settings.recycle_dir, "old.mp4"), b"x" * 100)
        self.write(os.path.join(self.settings.recycle_dir, "new.mp4"), b"x" * 100)
        stamp = time.time() - 7200
        os.utime(old, (stamp, stamp))

        settings = self.settings.replace(recycle_max_age_hours=1)
        removed, freed = engine.prune_recycle(settings)

        self.assertEqual(removed, 1)
        self.assertEqual(freed, 100)
        self.assertEqual(os.listdir(settings.recycle_dir), ["new.mp4"])

    def test_enforces_size_cap_oldest_first(self):
        for i in range(5):
            path = self.write(
                os.path.join(self.settings.recycle_dir, f"f{i}.bin"), b"x" * 400)
            stamp = time.time() - (100 - i)
            os.utime(path, (stamp, stamp))

        settings = self.settings.replace(recycle_max_age_hours=None,
                                         recycle_max_bytes=1000)
        engine.prune_recycle(settings)

        remaining = os.listdir(settings.recycle_dir)
        total = sum(os.path.getsize(os.path.join(settings.recycle_dir, f))
                    for f in remaining)
        self.assertLessEqual(total, 1000)
        # The newest files survive.
        self.assertIn("f4.bin", remaining)
        self.assertNotIn("f0.bin", remaining)

    def test_reports_age_and_size_removals(self):
        old = self.write(os.path.join(self.settings.recycle_dir, "old.mp4"), b"x" * 100)
        stamp = time.time() - 7200
        os.utime(old, (stamp, stamp))
        events = []
        settings = self.settings.replace(recycle_max_age_hours=1)
        engine.prune_recycle(settings, lambda n, d: events.append((n, d)))
        self.assertEqual(events[0][0], "recycle_pruned")
        self.assertEqual(events[0][1]["removed"], 1)

    def test_missing_dir_is_not_an_error(self):
        settings = self.settings.replace(recycle_dir=os.path.join(self.root, "nope"))
        self.assertEqual(engine.prune_recycle(settings), (0, 0))


# ── orphan sweep ─────────────────────────────────────────────────────

class OrphanSweep(TempWorkspace):
    def test_removes_partials_and_keeps_real_files(self):
        self.write(os.path.join(self.root, "stray.mp4.part"), b"x" * 50)
        self.write(os.path.join(self.settings.video_dir, "v_video.webm.part"), b"x" * 50)
        keep = self.write(os.path.join(self.settings.video_dir, "v_video.mp4"), b"x" * 50)

        removed, freed = engine.sweep_orphans(self.settings)

        self.assertEqual(removed, 2)
        self.assertEqual(freed, 100)
        self.assertFalse(os.path.exists(os.path.join(self.root, "stray.mp4.part")))
        self.assertTrue(os.path.exists(keep))

    def test_leaves_downloads_untouched(self):
        keep = self.write(os.path.join(self.settings.output_dir, "final.mp4"), b"x" * 10)
        engine.sweep_orphans(self.settings)
        self.assertTrue(os.path.exists(keep))


# ── classify_error ───────────────────────────────────────────────────

class ClassifyError(unittest.TestCase):
    def test_known_hints(self):
        cases = {
            "ERROR: Unsupported URL: https://x": "host not supported by yt-dlp",
            "Sign in to confirm you're not a bot": "YouTube bot check - try --cookies-from-browser",
            "HTTP Error 429: Too Many Requests": "rate limited",
            "Private video. Sign in": "private video",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(engine.classify_error(Exception(text)), expected)

    def test_falls_back_to_first_line(self):
        self.assertEqual(
            engine.classify_error(Exception("something odd happened")),
            "something odd happened")

    def test_empty_message_uses_type_name(self):
        self.assertEqual(engine.classify_error(ValueError()), "ValueError")


# ── container derivation ─────────────────────────────────────────────

def vfmt(**over):
    base = {"format_id": "1", "vcodec": "avc1.640028", "acodec": "none",
            "protocol": "https", "dynamic_range": "SDR", "height": 1080}
    base.update(over)
    return base


def afmt(**over):
    base = {"format_id": "2", "vcodec": "none", "acodec": "mp4a.40.2",
            "protocol": "https", "dynamic_range": "SDR", "abr": 128}
    base.update(over)
    return base


class ContainerDerivation(unittest.TestCase):
    def test_mp4_for_h264_aac(self):
        self.assertEqual(
            engine._container_for_streams(vfmt(), afmt()), "mp4")

    def test_mkv_for_opus_audio(self):
        """The old code emitted MP4 + Opus in compat mode."""
        self.assertEqual(
            engine._container_for_streams(vfmt(), afmt(acodec="opus", abr=160)),
            "mkv")

    def test_mkv_for_av1_with_opus(self):
        self.assertEqual(
            engine._container_for_streams(
                vfmt(vcodec="av01.0.12M.08", height=2160),
                afmt(acodec="opus", abr=160)),
            "mkv")

    def test_video_only_is_mp4(self):
        self.assertEqual(engine._container_for_streams(vfmt(), None), "mp4")


# ── merge ────────────────────────────────────────────────────────────

class Merge(TempWorkspace):
    def setUp(self):
        super().setUp()
        self.video = self.write(os.path.join(self.settings.video_dir, "v_video.mp4"),
                                b"video-bytes")
        self.audio = self.write(os.path.join(self.settings.audio_dir, "v_audio.m4a"),
                                b"audio-bytes")
        self.settings = self.settings.replace(ffmpeg_path="ffmpeg",
                                              ffprobe_path="ffprobe")

    def _fake_run(self, out_bytes=b"merged", returncode=0, stderr=""):
        """Stand-in for subprocess.run that writes the output file."""
        def fake(args, **kwargs):
            path = args[-1]
            if returncode == 0:
                with open(path, "wb") as fh:
                    fh.write(out_bytes)
            return subprocess.CompletedProcess(args, returncode,
                                               stdout="", stderr=stderr)
        return fake

    def _ok_probe(self):
        payload = {
            "streams": [
                {"codec_type": "video", "codec_name": "h264",
                 "height": 1080, "width": 1920, "avg_frame_rate": "30/1"},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"duration": "12.5"},
        }
        return subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")

    def _sequencer(self, *results):
        """Return a callable that yields each result in turn.

        Uses a plain function rather than Mock(side_effect=[...]): Mock does
        not *call* callables supplied as side effects, it returns them.
        """
        queue = list(results)
        calls = []

        def run(args, **kwargs):
            calls.append(args)
            result = queue.pop(0)
            return result(args) if callable(result) else result

        run.calls = calls
        return run

    def _run_ffmpeg_then_probe(self, out_bytes=b"merged"):
        """Two calls: ffmpeg (writes the file), then ffprobe (returns metadata)."""
        return self._sequencer(self._fake_run(out_bytes), self._ok_probe())

    def test_successful_merge_creates_output_and_recycles_sources(self):
        with mock.patch.object(engine.subprocess, "run",
                               new=self._run_ffmpeg_then_probe()):
            report = engine.merge(1, "My Title", self.video, self.audio,
                                  vfmt(), afmt(), self.settings)

        self.assertEqual(report["resolution"], "1920x1080")
        self.assertTrue(os.path.isfile(report["path"]))
        self.assertIn("My Title", os.path.basename(report["path"]))
        # Sources were moved into recycle/, not left in place.
        self.assertFalse(os.path.exists(self.video))
        self.assertFalse(os.path.exists(self.audio))
        self.assertEqual(len(os.listdir(self.settings.recycle_dir)), 2)

    def test_never_overwrites_an_existing_output(self):
        """The old code ran ffmpeg -y and silently replaced earlier files."""
        first = self.write(os.path.join(self.settings.output_dir, "01. My Title.mp4"),
                           b"original")
        with mock.patch.object(engine.subprocess, "run",
                               new=self._run_ffmpeg_then_probe()):
            report = engine.merge(1, "My Title", self.video, self.audio,
                                  vfmt(), afmt(), self.settings)

        self.assertNotEqual(report["path"], first)
        with open(first, "rb") as fh:
            self.assertEqual(fh.read(), b"original")
        self.assertIn("(2)", os.path.basename(report["path"]))

    def test_ffmpeg_failure_surfaces_stderr_and_removes_output(self):
        def always_fail(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom: bad codec")

        with mock.patch.object(engine.subprocess, "run",
                               new=self._sequencer(always_fail)):
            with self.assertRaises(engine.MergeError) as ctx:
                engine.merge(1, "T", self.video, self.audio,
                             vfmt(), afmt(), self.settings)
        self.assertIn("bad codec", str(ctx.exception))
        self.assertEqual(os.listdir(self.settings.output_dir), [])
        # Sources survive so the item can be retried.
        self.assertTrue(os.path.exists(self.video))

    def test_verification_failure_deletes_the_bad_output(self):
        bad = {"streams": [{"codec_type": "audio", "codec_name": "aac"}],
               "format": {"duration": "1"}}
        probe = subprocess.CompletedProcess([], 0, stdout=json.dumps(bad), stderr="")
        with mock.patch.object(engine.subprocess, "run",
                               new=self._sequencer(self._fake_run(), probe)):
            with self.assertRaises(engine.ValidationError):
                engine.merge(1, "T", self.video, self.audio, vfmt(), afmt(),
                             self.settings)
        self.assertEqual(os.listdir(self.settings.output_dir), [])

    def test_merge_command_has_loglevel_and_faststart(self):
        captured = {}

        def fake(args, **kwargs):
            captured["args"] = args
            with open(args[-1], "wb") as fh:
                fh.write(b"x")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with mock.patch.object(engine.subprocess, "run",
                               new=self._sequencer(fake, self._ok_probe())):
            engine.merge(1, "T", self.video, self.audio, vfmt(), afmt(), self.settings)

        self.assertIn("-loglevel", captured["args"])
        self.assertIn("+faststart", captured["args"])


# ── settings and paths ───────────────────────────────────────────────

class SettingsPaths(TempWorkspace):
    def test_paths_resolve_against_root_not_cwd(self):
        self.assertTrue(self.settings.output_dir.startswith(self.root))
        self.assertTrue(os.path.isabs(self.settings.output_dir))

    def test_import_does_not_create_directories(self):
        """Importing must not have filesystem side effects.

        Run the import inside a fresh, empty directory and assert nothing
        appeared there (the old module created video/ audio/ downloads/
        recycle/ at import time, relative to the CWD).
        """
        empty = make_workspace()
        try:
            env = dict(os.environ)
            env["PYTHONPATH"] = HERE
            probe = subprocess.run(
                [sys.executable, "-c",
                 "import downy.engine, downy.cli, downy.config"],
                capture_output=True, text=True, cwd=empty, env=env,
            )
            self.assertEqual(probe.returncode, 0, probe.stderr)
            self.assertEqual(os.listdir(empty), [])
        finally:
            shutil.rmtree(empty, ignore_errors=True)

    def test_ensure_dirs_creates_all_work_folders(self):
        for folder in self.settings.work_dirs:
            self.assertTrue(os.path.isdir(folder))

    def test_replace_returns_a_copy(self):
        other = self.settings.replace(mode="compat")
        self.assertEqual(other.mode, "compat")
        self.assertEqual(self.settings.mode, "best")

    def test_archive_path_lives_under_root(self):
        self.assertTrue(self.settings.archive_file.startswith(self.root))


class ModeResolution(unittest.TestCase):
    def test_cli_wins(self):
        self.assertEqual(
            config.resolve_mode("compat", {"mode": "efficient"}, {}), "compat")

    def test_config_beats_env(self):
        self.assertEqual(
            config.resolve_mode(None, {"mode": "efficient"},
                                {"DOWNY_MODE": "compat"}), "efficient")

    def test_env_beats_default(self):
        self.assertEqual(
            config.resolve_mode(None, {}, {"DOWNY_MODE": "compat"}), "compat")

    def test_legacy_env_still_honoured(self):
        self.assertEqual(
            config.resolve_mode(None, {}, {"DUNY_MODE": "compat"}), "compat")

    def test_default(self):
        self.assertEqual(config.resolve_mode(None, {}, {}), "best")

    def test_corrupt_config_is_ignored(self):
        tmp = make_workspace()
        try:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            self.assertEqual(config.read_config_file(path), {})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ── queue parsing ────────────────────────────────────────────────────

class QueueParsing(TempWorkspace):
    def _queue(self, text):
        path = os.path.join(self.root, "q.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return read_queue(path)

    def test_rejects_non_urls_and_credentials(self):
        urls = self._queue(
            "https://youtu.be/AAA\n"
            "sk-abcdef1234567890\n"
            "🥇 GLM-5.2 10.0\n"
            "=====\n"
            "fe_oa_aa835ce9a13d61fa9e7cb7ff86289e3f98bb3b74a7ce02b8\n"
        )
        self.assertEqual(urls, ["https://youtu.be/AAA"])

    def test_comments_and_sentinel(self):
        urls = self._queue(
            "# comment\n"
            "https://youtu.be/AAA\n"
            "---end---\n"
            "https://youtu.be/BBB\n"
        )
        self.assertEqual(urls, ["https://youtu.be/AAA"])

    def test_deduplicates(self):
        urls = self._queue("https://youtu.be/AAA\nhttps://youtu.be/AAA\n")
        self.assertEqual(urls, ["https://youtu.be/AAA"])

    def test_rescues_url_from_note_line(self):
        urls = self._queue("Pomeli AI - https://labs.google.com/x/\n")
        self.assertEqual(urls, ["https://labs.google.com/x/"])

    def test_strips_trailing_punctuation(self):
        urls = self._queue("See https://youtu.be/AAA.\n")
        self.assertEqual(urls, ["https://youtu.be/AAA"])

    def test_missing_file_returns_empty(self):
        self.assertEqual(read_queue(os.path.join(self.root, "nope.txt")), [])


# ── naming ───────────────────────────────────────────────────────────

class Naming(unittest.TestCase):
    def test_windows_reserved_names(self):
        for name in ("CON", "con", "NUL", "aux", "COM1", "LPT9", "CON.mp4"):
            with self.subTest(name=name):
                self.assertNotIn(
                    os.path.splitext(naming.safe_title(name))[0].lower(),
                    naming.RESERVED_NAMES)

    def test_trailing_dot_and_space_removed(self):
        self.assertEqual(naming.safe_title("ends with dot."), "ends with dot")
        self.assertEqual(naming.safe_title("trailing   "), "trailing")

    def test_control_characters_removed(self):
        out = naming.safe_title("a\r\nb\x00c")
        self.assertFalse(any(ord(c) < 32 for c in out))

    def test_empty_and_dots_get_fallback(self):
        self.assertEqual(naming.safe_title("   "), "video")
        self.assertEqual(naming.safe_title("..."), "video")

    def test_length_capped_and_extension_preserved(self):
        out = naming.safe_title("x" * 300)
        self.assertLessEqual(len(out), naming.MAX_TITLE_LENGTH)

    def test_illegal_chars_removed(self):
        self.assertEqual(naming.safe_title(r'a/b:c*d?e"f<g>h|i'), "abcdefghi")

    def test_is_idempotent(self):
        for raw in ("CON", "x" * 300, "sp ace ", "a\r\nb", "normal"):
            with self.subTest(raw=raw):
                once = naming.safe_title(raw)
                self.assertEqual(naming.safe_title(once), once)

    def test_unicode_is_preserved(self):
        title = "ලක්ෂ 3ක් ඉතිරි කරන සෙවන දැල් රහස"
        self.assertEqual(naming.safe_title(title), title)


class UniquePaths(TempWorkspace):
    def test_appends_counter_instead_of_overwriting(self):
        first = naming.build_output_path(self.settings.output_dir, 1, "Same", "mkv")
        self.write(first)
        second = naming.build_output_path(self.settings.output_dir, 1, "Same", "mkv")
        self.assertNotEqual(first, second)
        self.assertIn("(2)", second)

    def test_numbering_is_zero_padded(self):
        path = naming.build_output_path(self.settings.output_dir, 7, "Ep", "mp4")
        self.assertTrue(os.path.basename(path).startswith("07. "))

    def test_un_numbered_option(self):
        path = naming.build_output_path(
            self.settings.output_dir, 7, "Ep", "mp4", numbered=False)
        self.assertEqual(os.path.basename(path), "Ep.mp4")


# ── engine run_batch plumbing ────────────────────────────────────────

class RunBatch(TempWorkspace):
    def test_summary_and_events_with_stubbed_items(self):
        events = []
        calls = []

        def fake_run_item(url, index, total, settings, emit):
            calls.append((url, index))
            if "bad" in url:
                raise ValueError("nope")
            return {"path": "x", "resolution": "1x1", "fps": 1,
                    "video_codec": "h264", "audio_codec": "aac",
                    "audio_streams": 1, "duration": 1}

        with mock.patch.object(engine, "run_item", side_effect=fake_run_item):
            summary = engine.run_batch(
                ["https://ok", "https://bad"], self.settings,
                emit=lambda n, d: events.append(n))

        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(calls, [("https://ok", 1), ("https://bad", 2)])
        self.assertIn("item_ok", events)
        self.assertIn("item_failed", events)
        self.assertIn("batch_done", events)

    def test_should_stop_cancels_between_items(self):
        with mock.patch.object(engine, "run_item", return_value={"path": "x"}):
            summary = engine.run_batch(
                ["a", "b", "c"], self.settings, should_stop=lambda: True)
        self.assertEqual(summary["completed"], 0)
        self.assertEqual(summary["skipped"], 3)

    def test_sleep_interval_between_items_only(self):
        sleeps = []
        with mock.patch.object(engine, "run_item", return_value={"path": "x"}), \
             mock.patch.object(engine.time, "sleep", side_effect=sleeps.append):
            settings = self.settings.replace(sleep_interval=2.0)
            engine.run_batch(["a", "b", "c"], settings)
        self.assertEqual(sleeps, [2.0, 2.0])


# ── host support detection ───────────────────────────────────────────

class ArchiveTracking(TempWorkspace):
    def test_already_downloaded_is_false_initially(self):
        self.assertFalse(engine.already_downloaded("https://youtu.be/a", self.settings))

    def test_recording_makes_it_skippable(self):
        engine._record_archive("https://youtu.be/a", self.settings)
        self.assertTrue(engine.already_downloaded("https://youtu.be/a", self.settings))
        self.assertFalse(engine.already_downloaded("https://youtu.be/b", self.settings))

    def test_recording_is_idempotent(self):
        engine._record_archive("https://youtu.be/a", self.settings)
        engine._record_archive("https://youtu.be/a", self.settings)
        with open(self.settings.archive_file, encoding="utf-8") as fh:
            self.assertEqual(len(fh.readlines()), 1)

    def test_archive_disabled_means_nothing_is_skipped(self):
        engine._record_archive("https://youtu.be/a", self.settings)
        settings = self.settings.replace(use_archive=False)
        self.assertFalse(engine.already_downloaded("https://youtu.be/a", settings))

    def test_run_batch_skips_archived_urls(self):
        engine._record_archive("https://youtu.be/a", self.settings)
        events = []
        with mock.patch.object(engine, "run_item") as run_item_mock:
            summary = engine.run_batch(
                ["https://youtu.be/a"], self.settings,
                emit=lambda n, d: events.append(n))
            run_item_mock.assert_not_called()
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["completed"], 0)
        self.assertIn("item_skipped", events)

    def test_failed_item_is_not_archived(self):
        """A merge failure must not mark the video as done forever."""
        with mock.patch.object(engine, "probe", side_effect=ValueError("nope")):
            engine.run_batch(["https://youtu.be/a"], self.settings)
        self.assertFalse(engine.already_downloaded("https://youtu.be/a", self.settings))


class HostSupport(unittest.TestCase):
    def test_youtube_is_supported(self):
        self.assertTrue(engine.is_host_supported(
            "https://www.youtube.com/watch?v=GczjVOpaPPI"))

    def test_unknown_host_is_not_claimed(self):
        self.assertFalse(engine.is_host_supported(
            "https://cinesubz.lk/tvshows/money-heist-2021-sinhala-sub/"))


class OptionBuilding(TempWorkspace):
    """The yt-dlp options actually handed to the library."""

    def _opts(self, settings=None):
        return engine._base_opts(settings or self.settings, engine.EngineLogger())

    def test_archive_enabled_by_default(self):
        opts = self._opts()
        self.assertEqual(opts["download_archive"], self.settings.archive_file)

    def test_probe_never_uses_the_archive(self):
        """With the archive set, yt-dlp returns None for archived items."""
        opts = engine._base_opts(self.settings, engine.EngineLogger())
        self.assertIn("download_archive", opts)
        # probe() strips it before calling yt-dlp.
        with mock.patch.object(engine.yt_dlp, "YoutubeDL") as ydl_cls:
            ydl_cls.return_value.__enter__.return_value.extract_info.return_value = {
                "id": "x", "formats": []}
            engine.probe("https://youtu.be/x", self.settings)
            passed = ydl_cls.call_args[0][0]
            self.assertNotIn("download_archive", passed)

    def test_archive_can_be_disabled(self):
        opts = self._opts(self.settings.replace(use_archive=False))
        self.assertNotIn("download_archive", opts)

    def test_retries_follow_settings(self):
        opts = self._opts(self.settings.replace(download_attempts=9))
        self.assertEqual(opts["retries"], 9)
        self.assertEqual(opts["fragment_retries"], 9)

    def test_socket_timeout_follows_settings(self):
        opts = self._opts(self.settings.replace(socket_timeout=15))
        self.assertEqual(opts["socket_timeout"], 15)

    def test_ffmpeg_location_passed_when_known(self):
        settings = self.settings.replace(ffmpeg_path=r"C:\tools\ffmpeg.exe")
        opts = self._opts(settings)
        self.assertEqual(opts["ffmpeg_location"], r"C:\tools")

    def test_cookies_from_browser(self):
        opts = self._opts(self.settings.replace(cookies_from_browser="chrome"))
        self.assertEqual(opts["cookiesfrombrowser"], ("chrome",))

    def test_no_cookies_option_by_default(self):
        self.assertNotIn("cookiesfrombrowser", self._opts())
        self.assertIsNone(self._opts()["cookiefile"])


class FailFastHosts(TempWorkspace):
    def test_unsupported_host_is_rejected_before_any_network_call(self):
        with mock.patch.object(engine, "probe") as probe_mock:
            with self.assertRaises(engine.UnsupportedHostError):
                engine.run_item("https://cinesubz.lk/x/", 1, 1, self.settings)
            probe_mock.assert_not_called()

    def test_unsupported_host_label_is_clear(self):
        reason = engine.classify_error(
            engine.UnsupportedHostError("no extractor for this host: x"))
        self.assertEqual(reason, "host not supported by yt-dlp")

    def test_run_batch_records_host_rejections_as_failures(self):
        summary = engine.run_batch(["https://cinesubz.lk/x/"], self.settings)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["results"][0]["reason"],
                         "host not supported by yt-dlp")


if __name__ == "__main__":
    unittest.main(verbosity=2)





