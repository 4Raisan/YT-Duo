"""Same-source variants, repeat decisions, and queue-only filename prefixes."""
from pathlib import Path
from unittest import mock

from ytduo import engine, naming
from tests.test_engine import TempWorkspace, vfmt, afmt


FORMATS = [vfmt(format_id="h264", vbr=1000), afmt(),
           vfmt(format_id="vp9", vcodec="vp9", vbr=2000),
           vfmt(format_id="vp9-720", vcodec="vp9", height=720, vbr=800),
           afmt(format_id="opus", acodec="opus", abr=140)]
URL = "https://youtu.be/abcdefghijk"


class ExactDownloadHistory(TempWorkspace):
    def setUp(self):
        super().setUp()
        self.info = {"id": "abcdefghijk", "title": "Example", "formats": FORMATS}
        self.probe = mock.patch.object(engine, "probe", side_effect=lambda *args: self.info)
        self.fetch = mock.patch.object(engine, "fetch_stream", return_value=("synthetic", {}))
        self.merge = mock.patch.object(engine, "merge", side_effect=self.write_video)
        self.music = mock.patch.object(engine, "run_music", side_effect=self.write_music)
        for patch in (self.probe, self.fetch, self.merge, self.music):
            patch.start()
            self.addCleanup(patch.stop)

    def write_video(self, index, title, vfile, afile, video, audio, settings, emit):
        path = naming.build_output_path(settings.output_dir, index, title,
            engine._container_for_streams(video, audio), numbered=bool(settings.numbered))
        Path(path).write_bytes(b"verified synthetic output")
        return {"path": path}

    def write_music(self, url, index, title, formats, settings, emit, should_stop):
        Path(settings.media_output_dir).mkdir(parents=True, exist_ok=True)
        path = naming.build_output_path(settings.media_output_dir, index, title,
            "mp3" if settings.audio_format == "mp3" else "opus", numbered=bool(settings.numbered))
        Path(path).write_bytes(b"verified synthetic audio")
        return {"path": path}

    def download(self, settings=None, confirm=None, url=URL):
        return engine.run_item(url, 1, 1, settings or self.settings,
                               confirm_redownload=confirm)

    def test_different_container_resolution_media_or_mp3_quality_does_not_prompt(self):
        confirm = mock.Mock(return_value=False)
        variants = [self.settings, self.settings.replace(mode="compat"),
                    self.settings.replace(max_height=720),
                    self.settings.replace(media_type="audio"),
                    self.settings.replace(media_type="audio", audio_format="mp3", audio_bitrate=128),
                    self.settings.replace(media_type="audio", audio_format="mp3", audio_bitrate=320)]
        outputs = [self.download(s, confirm)["path"] for s in variants]
        confirm.assert_not_called()
        self.assertEqual(len(set(outputs)), 6)
        self.assertTrue(all(Path(p).is_file() for p in outputs))
        self.assertEqual(Path(outputs[0]).suffix, ".mkv")
        self.assertEqual(Path(outputs[1]).suffix, ".mp4")

    def test_exact_repeat_asks_and_skip_never_downloads_or_changes_original(self):
        report = self.download()
        original = Path(report["path"]).read_bytes()
        confirm = mock.Mock(return_value=False)
        engine.fetch_stream.reset_mock()
        self.assertTrue(self.download(confirm=confirm)["skipped"])
        confirm.assert_called_once_with(URL)
        engine.fetch_stream.assert_not_called()
        self.assertEqual(Path(report["path"]).read_bytes(), original)

    def test_approved_repeat_uses_a_unique_name_without_overwrite(self):
        first = self.download()["path"]
        second = self.download(confirm=lambda url: True)["path"]
        self.assertNotEqual(first, second)
        self.assertIn("(2)", Path(second).name)
        self.assertTrue(Path(first).is_file())

    def test_same_selected_streams_match_even_if_best_is_selected_manually(self):
        self.download()
        confirm = mock.Mock(return_value=False)
        self.download(self.settings.replace(max_height=1080), confirm)
        confirm.assert_called_once()

    def test_numeric_metadata_representation_does_not_change_the_identity(self):
        self.download()
        self.info = dict(self.info, formats=[dict(fmt, height=float(fmt["height"]))
            if fmt.get("height") else dict(fmt, abr=float(fmt["abr"])) for fmt in FORMATS])
        confirm = mock.Mock(return_value=False)
        self.download(confirm=confirm)
        confirm.assert_called_once()

    def test_muxed_opus_uses_mkv_in_the_recorded_identity(self):
        settings = self.settings
        formats = [vfmt(acodec="opus", abr=128)]
        self.assertEqual(engine._selection_identity(formats, settings)["container"], "mkv")

    def test_youtube_short_and_watch_links_match_the_same_video(self):
        self.download()
        confirm = mock.Mock(return_value=False)
        self.download(confirm=confirm, url="https://www.youtube.com/watch?v=abcdefghijk&list=ignored")
        confirm.assert_called_once()

    def test_missing_output_changed_destination_or_new_source_quality_is_not_a_repeat(self):
        first = self.download()["path"]
        Path(first).unlink()
        confirm = mock.Mock(return_value=False)
        self.download(confirm=confirm)
        other = self.settings.replace(output_dir="another-destination")
        other.ensure_dirs()
        self.download(other, confirm)
        self.info = dict(self.info, formats=FORMATS + [vfmt(format_id="new-2160", height=2160, vbr=8000)])
        self.download(confirm=confirm)
        confirm.assert_not_called()

    def test_legacy_url_only_history_does_not_block_other_variants(self):
        Path(self.settings.archive_file).write_text("YT-Duo " + URL + "\n")
        confirm = mock.Mock(return_value=False)
        self.download(confirm=confirm)
        confirm.assert_not_called()

    def test_cancel_after_metadata_prevents_download_and_duplicate_prompt(self):
        self.download()
        confirm = mock.Mock(return_value=True)
        with self.assertRaises(engine.Cancelled):
            engine.run_item(URL, 1, 1, self.settings, should_stop=lambda: True,
                            confirm_redownload=confirm)
        confirm.assert_not_called()


class QueueNaming(TempWorkspace):
    def run_names(self, urls, settings=None, take_more=None):
        names = []
        def item(url, index, total, settings, emit, should_stop, **kwargs):
            name = Path(naming.build_output_path(settings.output_dir, index, url, "mp4",
                                                numbered=bool(settings.numbered))).name
            names.append(name)
            if url == "fails":
                raise ValueError("synthetic failure")
            return {"path": name}
        with mock.patch.object(engine, "run_item", side_effect=item):
            engine.run_batch(urls, settings or self.settings, take_more=take_more)
        return names

    def test_single_is_plain_and_queue_positions_survive_a_failure(self):
        self.assertEqual(self.run_names(["Title"]), ["Title.mp4"])
        self.assertEqual(self.run_names(["First", "fails", "Third"]),
                         ["01. First.mp4", "02. fails.mp4", "03. Third.mp4"])

    def test_explicit_queue_and_cli_override_work_for_one_or_many_items(self):
        self.assertEqual(self.run_names(["One"], self.settings.replace(numbered=True)), ["01. One.mp4"])
        self.assertEqual(self.run_names(["One", "Two"], self.settings.replace(numbered=False)),
                         ["One.mp4", "Two.mp4"])

    def test_links_added_mid_download_get_their_queue_position(self):
        more = mock.Mock(side_effect=[["Second"], []])
        self.assertEqual(self.run_names(["First"], take_more=more), ["First.mp4", "02. Second.mp4"])
