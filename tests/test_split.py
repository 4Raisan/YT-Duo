"""Split safety, indexes, engine dispatch and time-range identities."""
import io
import os
import struct
import tempfile
import unittest
from unittest import mock

from ytduo import engine, split
from ytduo.config import Settings
from tests.test_gui import GuiTestCase


def mp4_fixture(version=0, nested=False):
    def box(kind, value):
        return struct.pack(">I4s", len(value) + 8, kind) + value
    init = box(b"ftyp", b"isom0000") + box(b"moov", b"metadata")
    timing = struct.pack(">QQ" if version else ">II", 0, 0)
    index = bytes([version, 0, 0, 0]) + struct.pack(">II", 1, 1000) + timing + struct.pack(">HH", 0, 100)
    for n in range(100):
        index += struct.pack(">III", 100 | (0x80000000 if nested else 0), 5000, 0)
    return init + box(b"sidx", index) + bytes(range(100)) * 100


class MemoryReader:
    MAX_READ = split.RangeReader.MAX_READ

    def __init__(self, data, ext="mp4"):
        self.data, self.size, self.fmt, self.reads = data, len(data), {"ext": ext}, []

    def read(self, first, last):
        self.reads.append((first, last))
        return self.data[first:last + 1]

    def block(self, start, size):
        return self.read(start, start + size - 1)


class Timing(unittest.TestCase):
    def test_time_forms(self):
        for value, expected in (("01:37:59", 5879), ("37:59.5", 2279.5), ("1115", 1115), (0, 0)):
            self.assertEqual(split.timestamp(value), expected)

    def test_invalid_times(self):
        for value in ("", "-1", "NaN", "inf", "1:60", "1:2:3:4", "00:xx", "1e3"):
            with self.subTest(value=value), self.assertRaises(split.SplitError):
                split.timestamp(value)

    def test_ranges(self):
        self.assertIsNone(split.validate(None, None))
        for a, b in ((None, 1), (1, None), (10, 10), (10, 9)):
            with self.assertRaises(split.SplitError):
                Settings(split_start=a, split_end=b)
        for info in ({"duration": 9}, {"duration": 20, "is_live": True}, {"duration": 20, "live_status": "post_live"}, {}):
            with self.assertRaises(split.SplitError):
                split.validate(1, 10, info)
        self.assertEqual(split.validate(1, 10, {"duration": 10, "live_status": "was_live"}), (1, 10))

    def test_copy_and_archive_identity(self):
        full = Settings()
        a = full.replace(split_start="1:00", split_end="2:00")
        b = a.replace(split_end=121)
        self.assertEqual(a.replace().split_start, 60)
        self.assertEqual(len({engine._archive_key("https://youtu.be/abc", s, {"format_id": 400})
                              for s in (full, a, b)}), 3)
        self.assertIn("00-01-00_00-02-00", split.suffix(60, 120))


class Indexes(unittest.TestCase):
    def test_edit_list_duration_cap_preserves_encoded_bytes(self):
        def box(kind, payload):
            return struct.pack(">I4s", len(payload) + 8, kind) + payload
        mvhd = bytearray(24)
        struct.pack_into(">II", mvhd, 12, 1000, 5500)
        tkhd = bytearray(28)
        struct.pack_into(">I", tkhd, 20, 5500)
        elst = struct.pack(">IIIihh", 0, 1, 5500, 200, 1, 0)
        moov = box(b"moov", box(b"mvhd", mvhd) + box(b"trak", box(b"tkhd", tkhd) + box(b"edts", box(b"elst", elst))))
        encoded = box(b"mdat", b"original-encoded-video-packets")
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "clip.mp4")
            with open(path, "wb") as stream:
                stream.write(moov + encoded)
            split.cap_mp4_duration(path, 5)
            with open(path, "rb") as stream:
                result = stream.read()
            self.assertEqual(len(result), len(moov + encoded))
            self.assertEqual(result[-len(encoded):], encoded)
            self.assertNotIn(struct.pack(">I", 5500), result)
            before = result
            split.cap_mp4_duration(path, 6)  # Never extend absent source samples.
            with open(path, "rb") as stream:
                self.assertEqual(stream.read(), before)

    def test_sidx_versions_and_bounded_selection(self):
        for version in (0, 1):
            reader = MemoryReader(mp4_fixture(version))
            init, rows = split.plan(reader, 100, 110, 500)
            self.assertEqual([(r[0], r[1]) for r in rows], [(90, 95), (95, 100), (100, 105), (105, 110), (110, 115)])
            self.assertLess(max(b for a, b in reader.reads), len(reader.data) // 2)
            with tempfile.TemporaryDirectory() as folder:
                path = os.path.join(folder, "clip.mp4")
                events = list(split.transfer(reader, (init, rows), path))
                self.assertEqual(events[-1], (500, 500))
                with open(path, "rb") as stream:
                    self.assertEqual(stream.read(), init + reader.data[rows[0][2]:rows[-1][3]+1])

    def test_nested_index_refused_before_media(self):
        with self.assertRaisesRegex(split.SplitError, "Nested"):
            split.plan(MemoryReader(mp4_fixture(nested=True)), 100, 110, 500)

    def test_bad_index_and_unsupported_container(self):
        for reader in (MemoryReader(b"\0" * 64), MemoryReader(b"data", "m3u8")):
            with self.assertRaises(split.SplitError):
                split.plan(reader, 100, 110, 500)
        with self.assertRaises(split.SplitError):
            list(split.elements(b"\xbb\xff\0"))
        with self.assertRaises(split.SplitError):
            split.vint(b"\0")


class Response(io.BytesIO):
    def __init__(self, data, headers, status=200):
        super().__init__(data)
        self.headers, self.status = headers, status


class Transport(unittest.TestCase):
    def reader(self, response, url="https://sample.googlevideo.com/videoplayback"):
        session = mock.Mock()
        session.urlopen.return_value = response
        reader = split.RangeReader(session, {"url": url, "filesize": 1000, "protocol": "https"},
                                   Settings(download_attempts=1), lambda: None)
        return reader, session

    def test_google_query_not_duplicate_range_header(self):
        reader, session = self.reader(Response(b"abc", {"Content-Length": "3"}))
        self.assertEqual(reader.read(20, 22), b"abc")
        request = session.urlopen.call_args.args[0]
        self.assertIn("range=20-22", request.url)
        self.assertNotIn("Range", request.headers)

    def test_ignored_range_never_read(self):
        response = Response(b"x" * 1000, {"Content-Length": "1000"})
        reader, _ = self.reader(response)
        with mock.patch.object(response, "read", wraps=response.read) as read:
            with self.assertRaisesRegex(split.SplitError, "ignored"):
                reader.read(20, 22)
            read.assert_not_called()

    def test_overflow_stops_after_expected_plus_one(self):
        reader, _ = self.reader(Response(b"x" * 1000, {}))
        with self.assertRaisesRegex(split.SplitError, "exceeded"):
            reader.read(20, 22)
        self.assertEqual(reader.received, 4)

    def test_generic_server_requires_exact_content_range(self):
        reader, _ = self.reader(Response(b"abc", {"Content-Range": "bytes 20-22/1000"}, 206), "https://example.com/source.mp4")
        self.assertEqual(reader.read(20, 22), b"abc")
        reader, _ = self.reader(Response(b"abc", {"Content-Range": "bytes 0-2/1000"}, 206))
        with self.assertRaises(split.SplitError):
            reader.read(20, 22)

    def test_cancel_before_network(self):
        reader, session = self.reader(Response(b"abc", {}))
        reader.check = mock.Mock(side_effect=engine.Cancelled)
        with self.assertRaises(engine.Cancelled):
            reader.read(20, 22)
        session.urlopen.assert_not_called()

    def test_unknown_size_and_streaming_manifest_refused(self):
        for fmt in ({"filesize": None}, {"filesize": 1000, "protocol": "m3u8_native"}):
            with self.assertRaises(split.SplitError):
                split.RangeReader(mock.Mock(), fmt, Settings(), lambda: None)


class Dispatch(unittest.TestCase):
    def test_split_never_calls_full_fetch(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings(root=folder, split_start=100, split_end=110, use_archive=False,
                                require_supported_host=False)
            info = {"id": "abc", "title": "Clip", "duration": 500, "formats": []}
            with mock.patch.object(engine, "probe", return_value=info), \
                 mock.patch.object(engine, "_selection_identity", return_value={}), \
                 mock.patch.object(split, "run", side_effect=split.SplitError("Unsupported index")), \
                 mock.patch.object(engine, "fetch_stream") as full:
                result = engine.run_batch(["test"], settings)
                self.assertEqual(result["failed"], 1)
                full.assert_not_called()


class SplitGui(GuiTestCase):
    def test_queue_uses_its_own_metadata_not_the_entry_preview(self):
        self.app.split_var.set(True)
        self.app.split_start_var.set("60")
        self.app.split_end_var.set("120")
        self.app._preview_info = {"duration": 10}
        with mock.patch.object(self.app, "_preflight", return_value=False) as preflight:
            self.app._start(["https://youtu.be/another-video"], queued=True)
            preflight.assert_called_once()

    def test_split_is_opt_in_and_uses_selected_video_or_music_settings(self):
        self.assertFalse(self.app.split_var.get())
        self.assertIsNone(self.app._make_settings(include_split=True).split_start)
        self.app.split_var.set(True)
        self.app._toggle_split()
        self.app.split_start_var.set("01:37:59")
        self.app.split_end_var.set("01:56:34")
        current = self.app._make_settings(include_split=True)
        self.assertEqual((current.split_start, current.split_end), (5879, 6994))
        self.app.media_var.set("audio")
        self.assertEqual(self.app._make_settings(include_split=True).media_type, "audio")
        self.app.split_var.set(False)
        self.app._toggle_split()
        self.assertIsNone(self.app._make_settings(include_split=True).split_start)
        self.assertFalse(self.app.split_row.winfo_manager())

    def test_invalid_split_does_not_start_worker_or_break_preview(self):
        self.app.split_var.set(True)
        self.app.split_start_var.set("broken")
        self.app._make_settings()  # A quality scan is independent of incomplete edits.
        with mock.patch.object(self.app, "_preflight") as preflight:
            self.assertFalse(self.app._start(["https://youtu.be/abc"]))
            preflight.assert_not_called()

    def test_split_controls_follow_theme_and_font_scale(self):
        self.app.split_var.set(True)
        self.app._toggle_split()
        self.app.apply_font_scale(125, save=False)
        self.app.apply_theme("light")
        for entry in self.app.split_entries:
            self.assertEqual(entry.cget("bg"), self.app.palette["surface_alt"])
