"""Offline tests for selector.py.

Uses tests/fixtures/formats.json, captured from live yt-dlp metadata via
capture_fixtures.py. No network access.

Run:  python -m unittest test_selector -v
"""
import json
import os
import re
import unittest

from ytduo import selector
from ytduo.selector import NoSuitableFormat

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
FIXTURES = os.path.join(HERE, "tests", "fixtures", "formats.json")

VIDEO_IDS = [
    "X98-gKKR4Bk", "8qZELzy3-LY", "GczjVOpaPPI", "4rWx4cBh684",
    "tG1gzsHM5m8", "ULviSuJu4Tk", "1RXejVzyM4U", "f9hMDlqPMf4",
]

ITAG = re.compile(r"^(sb\d|\d{2,3}(-drc)?)$")


def load_fixtures():
    with open(FIXTURES, encoding="utf-8") as fh:
        return json.load(fh)


DATA = load_fixtures()


def video_formats(fixtures):
    return [f for f in fixtures["formats"] if selector.vcodec_of(f)]


def audio_formats(fixtures):
    return [f for f in fixtures["formats"] if not selector.vcodec_of(f)]


def clean_audio(fixtures):
    return [
        f for f in audio_formats(fixtures)
        if not selector.is_drc(f) and selector.bitrate_of(f, audio=True) > 0
    ]


def synthetic(**over):
    base = {
        "format_id": "x", "ext": "mp4", "vcodec": "none", "acodec": "none",
        "protocol": "https", "height": None, "fps": None,
        "dynamic_range": "SDR", "has_drm": False,
    }
    base.update(over)
    return base


def vfmt(**over):
    defaults = {"vcodec": "av01.0.12M.08", "acodec": "none", "vbr": 2000}
    defaults.update(over)
    return synthetic(**defaults)


def afmt(**over):
    defaults = {"vcodec": "none", "acodec": "opus", "abr": 128}
    defaults.update(over)
    return synthetic(**defaults)


class FixtureIntegrity(unittest.TestCase):
    def test_all_eight_videos_present(self):
        self.assertEqual(sorted(DATA), sorted(VIDEO_IDS))

    def test_every_video_has_video_and_audio_streams(self):
        for vid, data in DATA.items():
            self.assertTrue(video_formats(data), f"{vid} has no video formats")
            self.assertTrue(audio_formats(data), f"{vid} has no audio formats")

    def test_no_hdr_in_corpus(self):
        """HDR code paths cannot be validated against real data."""
        seen = {
            selector.dynamic_range_of(f)
            for data in DATA.values()
            for f in data["formats"]
        }
        self.assertEqual(seen, {"", "sdr"})


class BestModeRealVideos(unittest.TestCase):
    EXPECTED = {
        "X98-gKKR4Bk":  (1080, 50.0),
        "8qZELzy3-LY":  (1920, 30.0),
        "GczjVOpaPPI":  (720, 24.0),
        "4rWx4cBh684":  (1080, 50.0),
        "tG1gzsHM5m8":  (2160, 25.0),
        "ULviSuJu4Tk":  (2160, 25.0),
        "1RXejVzyM4U":  (2160, 25.0),
        "f9hMDlqPMf4":  (2160, 30.0),
    }

    def test_picks_maximum_resolution_and_fps(self):
        for vid, (height, fps) in self.EXPECTED.items():
            with self.subTest(vid=vid):
                picked = selector.pick_video(DATA[vid]["formats"], "best")
                self.assertEqual(selector.height_of(picked), height)
                self.assertEqual(selector.fps_of(picked), fps)

    def test_never_returns_storyboards(self):
        for vid, data in DATA.items():
            with self.subTest(vid=vid):
                picked = selector.pick_video(data["formats"], "best")
                self.assertFalse(selector.is_storyboard(picked))

    def test_never_returns_audio_only_for_video(self):
        for vid, data in DATA.items():
            with self.subTest(vid=vid):
                picked = selector.pick_video(data["formats"], "best")
                self.assertIsNotNone(selector.vcodec_of(picked))
                self.assertIsNone(selector.acodec_of(picked))

    def test_prefers_best_opus_audio(self):
        for vid, data in DATA.items():
            with self.subTest(vid=vid):
                picked = selector.pick_audio(data["formats"], "best")
                self.assertEqual(
                    selector.codec_family(selector.acodec_of(picked)), "opus"
                )
                best_opus = max(
                    selector.bitrate_of(f, audio=True)
                    for f in clean_audio(data)
                    if selector.codec_family(selector.acodec_of(f)) == "opus"
                )
                self.assertAlmostEqual(
                    selector.bitrate_of(picked, audio=True), best_opus, places=1
                )

    def test_audio_bitrate_stays_near_maximum(self):
        for vid, data in DATA.items():
            with self.subTest(vid=vid):
                picked = selector.pick_audio(data["formats"], "best")
                ceiling = max(
                    selector.bitrate_of(f, audio=True) for f in clean_audio(data)
                )
                self.assertGreaterEqual(
                    selector.bitrate_of(picked, audio=True), ceiling * 0.85
                )


class MissingCodecCoverage(unittest.TestCase):
    def test_video_with_no_av1_falls_back_to_vp9_or_h264(self):
        data = DATA["X98-gKKR4Bk"]
        families = {selector.codec_family(selector.vcodec_of(f))
                    for f in video_formats(data)}
        self.assertNotIn("av01", families)

        picked = selector.pick_video(data["formats"], "best")
        self.assertIsNotNone(selector.vcodec_of(picked))
        self.assertEqual(selector.height_of(picked), 1080)

    def test_h264_only_tier_wins_over_lower_res_vp9(self):
        """X98-gKKR4Bk tops out at 1080p50 H.264 with VP9 only at 360p."""
        data = DATA["X98-gKKR4Bk"]
        vp9_heights = [
            selector.height_of(f) for f in video_formats(data)
            if selector.codec_family(selector.vcodec_of(f)) == "vp9"
        ]
        picked = selector.pick_video(data["formats"], "best")
        self.assertGreater(selector.height_of(picked), max(vp9_heights))
        self.assertEqual(selector.codec_family(selector.vcodec_of(picked)), "avc1")

    def test_non_1080p_height_is_not_lost(self):
        """8qZELzy3-LY's nominal 1080p is 1920x960."""
        picked = selector.pick_video(DATA["8qZELzy3-LY"]["formats"], "best")
        self.assertEqual(selector.height_of(picked), 1920)

    def test_low_ceiling_video_uses_its_own_maximum(self):
        picked = selector.pick_video(DATA["GczjVOpaPPI"]["formats"], "best")
        self.assertEqual(selector.height_of(picked), 720)


class ProtocolPreference(unittest.TestCase):
    def test_excludes_mhtml_storyboards(self):
        for vid, data in DATA.items():
            with self.subTest(vid=vid):
                picked = selector.pick_video(data["formats"], "best")
                self.assertNotEqual(picked.get("protocol"), "mhtml")

    def test_direct_protocol_preferred_over_fragmented(self):
        for vid, data in DATA.items():
            for mode in ("best", "efficient", "compat"):
                with self.subTest(vid=vid, mode=mode):
                    picked = selector.pick_video(data["formats"], mode)
                    self.assertEqual(picked.get("protocol"), "https")

    def test_hls_is_excluded_unless_enabled(self):
        for vid, data in DATA.items():
            with self.subTest(vid=vid):
                picked = selector.pick_video(data["formats"], "best")
                self.assertNotEqual(picked.get("protocol"), "m3u8_native")


class DRCHandling(unittest.TestCase):
    def test_drc_audio_never_selected(self):
        for vid, data in DATA.items():
            for mode in ("best", "efficient", "compat"):
                with self.subTest(vid=vid, mode=mode):
                    picked = selector.pick_audio(data["formats"], mode)
                    self.assertFalse(selector.is_drc(picked))

    def test_drc_detected_by_id_and_note(self):
        self.assertTrue(selector.is_drc({"format_id": "251-drc"}))
        self.assertTrue(selector.is_drc(
            {"format_id": "140", "format_note": "medium, DRC"}))
        self.assertFalse(selector.is_drc(
            {"format_id": "140", "format_note": "medium"}))


class CompatMode(unittest.TestCase):
    def test_prefers_h264_video_and_aac_audio(self):
        for vid, data in DATA.items():
            with self.subTest(vid=vid):
                video = selector.pick_video(data["formats"], "compat")
                audio = selector.pick_audio(data["formats"], "compat")
                self.assertEqual(
                    selector.codec_family(selector.vcodec_of(video)), "avc1")
                self.assertEqual(
                    selector.codec_family(selector.acodec_of(audio)), "mp4a")

    def test_compat_container_is_mp4(self):
        self.assertEqual(selector.container_for("compat"), "mp4")

    def test_best_container_is_mkv(self):
        self.assertEqual(selector.container_for("best"), "mkv")
        self.assertEqual(selector.container_for("efficient"), "mkv")

    def test_compat_follows_availability_not_a_hardcoded_cap(self):
        """A taller H.264 stream must win; there is no 1080 ceiling."""
        formats = [
            vfmt(format_id="tall", vcodec="avc1.640028", height=2160, fps=30, vbr=9000),
            vfmt(format_id="short", vcodec="avc1.640028", height=1080, fps=30, vbr=4000),
        ]
        picked = selector.pick_video(formats, "compat")
        self.assertEqual(picked["format_id"], "tall")

    def test_compat_prefers_h264_even_when_taller_av1_exists(self):
        formats = [
            vfmt(format_id="av1tall", vcodec="av01.0.12M.08", height=2160, fps=30, vbr=3000),
            vfmt(format_id="h264", vcodec="avc1.640028", height=1080, fps=30, vbr=4000),
        ]
        picked = selector.pick_video(formats, "compat")
        self.assertEqual(picked["format_id"], "h264")


class EfficientMode(unittest.TestCase):
    def test_av1_preferred_over_vp9_at_equal_quality(self):
        formats = [
            vfmt(format_id="vp9stream", vcodec="vp9", vbr=2000, height=2160, fps=30),
            vfmt(format_id="av1stream", vcodec="av01.0.12M.08", vbr=2050,
                 height=2160, fps=30),
        ]
        picked = selector.pick_video(formats, "efficient")
        self.assertEqual(selector.codec_family(selector.vcodec_of(picked)), "av01")

    def test_best_mode_keeps_higher_bitrate_over_av1_efficiency(self):
        formats = [
            vfmt(format_id="vp9stream", vcodec="vp9", vbr=7295, height=2160, fps=25),
            vfmt(format_id="av1stream", vcodec="av01.0.12M.08", vbr=2126,
                 height=2160, fps=25),
        ]
        picked = selector.pick_video(formats, "best")
        self.assertEqual(selector.codec_family(selector.vcodec_of(picked)), "vp9")

    def test_real_2160p_selection_differs_across_modes(self):
        formats = DATA["tG1gzsHM5m8"]["formats"]
        best = selector.pick_video(formats, "best")
        efficient = selector.pick_video(formats, "efficient")
        self.assertEqual(selector.height_of(best), 2160)
        self.assertEqual(selector.height_of(efficient), 2160)
        self.assertNotEqual(best["format_id"], efficient["format_id"])
        self.assertEqual(
            selector.codec_family(selector.vcodec_of(efficient)), "av01")

    def test_efficient_is_smaller_on_real_data(self):
        formats = DATA["tG1gzsHM5m8"]["formats"]
        best = selector.pick_video(formats, "best")
        efficient = selector.pick_video(formats, "efficient")
        self.assertLess(
            selector.bitrate_of(efficient), selector.bitrate_of(best))


class TieBreaking(unittest.TestCase):
    def test_identical_tiers_resolve_deterministically(self):
        formats = [
            vfmt(format_id="aaa", vcodec="av01.0.12M.08", vbr=2000, height=1080, fps=30),
            vfmt(format_id="bbb", vcodec="av01.0.12M.08", vbr=2000, height=1080, fps=30),
        ]
        first = selector.pick_video(formats, "best")["format_id"]
        second = selector.pick_video(formats, "best")["format_id"]
        self.assertEqual(first, second)

    def test_higher_fps_wins_within_same_height(self):
        formats = [
            vfmt(format_id="lowfps", height=1080, fps=24, vbr=2000),
            vfmt(format_id="highfps", height=1080, fps=60, vbr=2000),
        ]
        self.assertEqual(
            selector.pick_video(formats, "best")["format_id"], "highfps")

    def test_missing_fps_does_not_beat_known_fps_at_same_height(self):
        formats = [
            vfmt(format_id="nofps", height=2160, fps=None, vbr=2000),
            vfmt(format_id="withfps", height=1080, fps=30, vbr=2000),
        ]
        self.assertEqual(
            selector.pick_video(formats, "best")["format_id"], "nofps")

    def test_direct_protocol_breaks_equal_bitrate_tie(self):
        formats = [
            vfmt(format_id="hls", height=1080, fps=30, vbr=2000,
                 protocol="m3u8_native"),
            vfmt(format_id="direct", height=1080, fps=30, vbr=2000,
                 protocol="https"),
        ]
        self.assertEqual(
            selector.pick_video(formats, "best")["format_id"], "direct")


class FallbackBehaviour(unittest.TestCase):
    def test_muxed_used_when_no_video_only_stream(self):
        formats = [synthetic(vcodec="avc1.640028", acodec="mp4a.40.2",
                             height=720, fps=30, vbr=1500)]
        picked = selector.pick_video(formats, "best")
        self.assertIsNotNone(selector.acodec_of(picked))

    def test_pick_audio_reports_absent_audio(self):
        with self.assertRaises(NoSuitableFormat):
            selector.pick_audio([vfmt(height=1080, fps=30, vbr=2000)], "best")

    def test_pick_streams_flags_no_merge_needed(self):
        video, audio, merged = selector.pick_streams(
            DATA["tG1gzsHM5m8"]["formats"], "best")
        self.assertTrue(merged)
        self.assertIsNotNone(video)
        self.assertIsNotNone(audio)

    def test_pick_streams_flags_muxed_only(self):
        formats = [synthetic(vcodec="avc1.640028", acodec="mp4a.40.2",
                             height=720, fps=30, vbr=1500)]
        video, audio, merged = selector.pick_streams(formats, "best")
        self.assertFalse(merged)
        self.assertIsNone(audio)
        self.assertTrue(selector.is_muxed(video))

    def test_drm_formats_are_never_selected(self):
        formats = [
            vfmt(format_id="drm", has_drm=True, height=2160, fps=30, vbr=9000),
            vfmt(format_id="clean", has_drm=False, height=1080, fps=30, vbr=2000),
        ]
        self.assertEqual(
            selector.pick_video(formats, "best")["format_id"], "clean")

    def test_unsupported_protocol_is_excluded(self):
        formats = [
            vfmt(format_id="weird", height=2160, fps=30, vbr=9000,
                 protocol="rtmp"),
            vfmt(format_id="normal", height=1080, fps=30, vbr=2000),
        ]
        self.assertEqual(
            selector.pick_video(formats, "best")["format_id"], "normal")


class ResolutionCap(unittest.TestCase):
    """--resolution / GUI quality picker."""

    def test_cap_lowers_the_tier(self):
        formats = DATA["1RXejVzyM4U"]["formats"]  # offers 2160p
        picked = selector.pick_video(formats, "best", max_height=720)
        self.assertLessEqual(selector.height_of(picked), 720)

    def test_cap_picks_the_highest_tier_under_it(self):
        formats = DATA["1RXejVzyM4U"]["formats"]
        heights = selector.available_heights(formats, "best")
        under = [h for h in heights if h <= 1080]
        picked = selector.pick_video(formats, "best", max_height=1080)
        self.assertEqual(selector.height_of(picked), max(under))

    def test_cap_above_what_is_offered_changes_nothing(self):
        formats = DATA["GczjVOpaPPI"]["formats"]  # tops out at 720
        uncapped = selector.pick_video(formats, "best")
        capped = selector.pick_video(formats, "best", max_height=4320)
        self.assertEqual(uncapped["format_id"], capped["format_id"])

    def test_impossible_cap_falls_back_instead_of_raising(self):
        """A 100p cap on a 720p-only video must still produce a download."""
        formats = DATA["GczjVOpaPPI"]["formats"]
        picked = selector.pick_video(formats, "best", max_height=100)
        self.assertIsNotNone(selector.vcodec_of(picked))

    def test_none_cap_is_unlimited(self):
        formats = DATA["1RXejVzyM4U"]["formats"]
        self.assertEqual(
            selector.pick_video(formats, "best")["format_id"],
            selector.pick_video(formats, "best", max_height=None)["format_id"])

    def test_cap_applies_through_pick_streams(self):
        video, audio, merged = selector.pick_streams(
            DATA["1RXejVzyM4U"]["formats"], "best", max_height=480)
        self.assertLessEqual(selector.height_of(video), 480)
        self.assertTrue(merged)
        self.assertIsNotNone(audio)

    def test_available_heights_is_descending_and_unique(self):
        for vid, data in DATA.items():
            with self.subTest(vid=vid):
                heights = selector.available_heights(data["formats"], "best")
                self.assertEqual(heights, sorted(set(heights), reverse=True))
                self.assertTrue(all(h > 0 for h in heights))


class EmptyAndDegenerate(unittest.TestCase):
    def test_no_formats_raises(self):
        with self.assertRaises(NoSuitableFormat):
            selector.pick_video([], "best")

    def test_storyboards_only_raises(self):
        formats = [synthetic(vcodec="images", acodec="none", protocol="mhtml")]
        with self.assertRaises(NoSuitableFormat):
            selector.pick_video(formats, "best")

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            selector.pick_video(DATA["tG1gzsHM5m8"]["formats"], "turbo")

    def test_resolution_string_used_when_height_missing(self):
        fmt = synthetic(vcodec="avc1.640028", acodec="none", height=None,
                        resolution="1920x1080", fps=30, vbr=2000)
        self.assertEqual(selector.height_of(fmt), 1080)

    def test_malformed_bitrate_does_not_raise(self):
        fmt = vfmt(height=1080, fps=30, vbr=None, tbr=None)
        self.assertEqual(selector.bitrate_of(fmt), 0.0)

    def test_malformed_resolution_does_not_raise(self):
        fmt = vfmt(height=None, resolution="weird", fps=30)
        self.assertEqual(selector.height_of(fmt), 0.0)

    def test_describe_handles_none(self):
        self.assertEqual(selector.describe(None), "none")


class HDRBehaviour(unittest.TestCase):
    """Synthetic only -- no fixture in the corpus contains HDR."""

    def test_corpus_cannot_validate_hdr(self):
        hdr_present = any(
            selector.dynamic_range_of(f) in selector.HDR_RANGES
            for data in DATA.values()
            for f in data["formats"]
        )
        self.assertFalse(hdr_present)

    def test_hdr_ignored_by_default(self):
        formats = [
            vfmt(format_id="sdr", height=1080, fps=30, vbr=2000,
                 dynamic_range="SDR"),
            vfmt(format_id="hdr", height=1080, fps=30, vbr=2100,
                 dynamic_range="HDR10"),
        ]
        self.assertEqual(selector.pick_video(formats, "best")["format_id"], "sdr")

    def test_hdr_selected_when_allowed(self):
        original = selector.MODES["best"]
        selector.MODES["best"] = dict(original, allow_hdr=True)
        try:
            formats = [
                vfmt(format_id="sdr", height=1080, fps=30, vbr=2000,
                     dynamic_range="SDR"),
                vfmt(format_id="hdr", height=1080, fps=30, vbr=2100,
                     dynamic_range="HDR10"),
            ]
            self.assertEqual(
                selector.pick_video(formats, "best")["format_id"], "hdr")
        finally:
            selector.MODES["best"] = original


class NoHardcodedFormatIds(unittest.TestCase):
    def test_selector_source_contains_no_itag_literals(self):
        path = os.path.join(HERE, "src", "ytduo", "selector.py")
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        source = re.sub(r'"""[\s\S]*?"""', "", source)
        found = re.findall(r"[\"'](\d{2,3})[\"']", source)
        self.assertEqual(found, [], f"format ids hardcoded: {found}")

    def test_every_fixture_id_is_known_shape(self):
        for vid, data in DATA.items():
            for fmt in data["formats"]:
                self.assertRegex(fmt["format_id"], ITAG)


class CodecFamilyParsing(unittest.TestCase):
    def test_families(self):
        cases = {
            "av01.0.12M.08": "av01",
            "vp09.00.50.08": "vp9",
            "vp9": "vp9",
            "avc1.640028": "avc1",
            "h264": "avc1",
            "mp4a.40.2": "mp4a",
            "opus": "opus",
        }
        for codec, expected in cases.items():
            with self.subTest(codec=codec):
                self.assertEqual(selector.codec_family(codec), expected)


class EveryModeProducesUsableStreams(unittest.TestCase):
    def test_best_and_efficient_reach_the_same_maximum_tier(self):
        for vid, data in DATA.items():
            with self.subTest(vid=vid):
                best = selector.height_of(selector.pick_video(data["formats"], "best"))
                efficient = selector.height_of(
                    selector.pick_video(data["formats"], "efficient"))
                self.assertEqual(best, efficient)

    def test_no_mode_exceeds_the_available_maximum(self):
        for vid, data in DATA.items():
            ceiling = max(selector.height_of(f) for f in video_formats(data))
            for mode in selector.MODES:
                with self.subTest(vid=vid, mode=mode):
                    picked = selector.pick_video(data["formats"], mode)
                    self.assertLessEqual(selector.height_of(picked), ceiling)

    def test_compat_never_outranks_maximum_quality_modes(self):
        """compat trades resolution for codec, by design."""
        for vid, data in DATA.items():
            with self.subTest(vid=vid):
                best = selector.height_of(selector.pick_video(data["formats"], "best"))
                compat = selector.height_of(
                    selector.pick_video(data["formats"], "compat"))
                self.assertLessEqual(compat, best)

    def test_best_and_efficient_pick_the_same_audio_bitrate(self):
        for vid, data in DATA.items():
            with self.subTest(vid=vid):
                best = selector.bitrate_of(
                    selector.pick_audio(data["formats"], "best"), audio=True)
                efficient = selector.bitrate_of(
                    selector.pick_audio(data["formats"], "efficient"), audio=True)
                self.assertAlmostEqual(best, efficient, places=1)

    def test_every_mode_returns_a_playable_pair(self):
        for vid, data in DATA.items():
            for mode in selector.MODES:
                with self.subTest(vid=vid, mode=mode):
                    video, audio, merged = selector.pick_streams(
                        data["formats"], mode)
                    self.assertIsNotNone(selector.vcodec_of(video))
                    if merged:
                        self.assertIsNotNone(selector.acodec_of(audio))
                    self.assertIn(selector.container_for(mode), ("mkv", "mp4"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
