"""Terminal front-end.

Everything user-facing lives here: argument parsing, console rendering,
prompts, and the exit code. The engine below it knows nothing about any
of that, which is what lets a GUI or web front-end reuse the same core.

Run ``python -m downy --help`` for usage.
"""

import argparse
import os
import re
import sys

from . import __version__, engine, selector
from .config import Settings, read_config_file, resolve_mode

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def extract_urls(text):
    """Return the URLs embedded in free-form text, punctuation trimmed.

    Shared by the terminal front-end and the GUI so both accept exactly
    the same input.
    """
    out = []
    for line in (text or "").splitlines():
        for url in URL_RE.findall(line):
            cleaned = url.rstrip(".,;:!?\"')]")
            if cleaned:
                out.append(cleaned)
    return out


# ── Argument parsing ─────────────────────────────────────────────────

class ModeAction(argparse.Action):
    """Validate --mode against the modes selector.py actually defines."""

    def __call__(self, parser, namespace, values, option_string=None):
        if values not in selector.MODES:
            parser.error(
                f"invalid mode {values!r} (choose from {', '.join(sorted(selector.MODES))})")
        setattr(namespace, self.dest, values)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="downy",
        description="YT-Duo - batch video downloader.",
        epilog=(
            "examples:\n"
            "  python -m downy                      # read links.txt\n"
            "  python -m downy --mode compat        # H.264/AAC in MP4\n"
            "  python -m downy -m efficient         # smaller AV1 encodes\n"
            "  python -m downy https://youtu.be/ID  # one URL, no queue file\n"
            "  python -m downy --dry-run            # preview, download nothing\n"
            "  python -m downy --list-formats URL   # inspect available streams\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "urls", nargs="*", metavar="URL",
        help="URLs to download (default: read from links.txt)")
    parser.add_argument(
        "-m", "--mode", action=ModeAction, default=None,
        metavar="MODE",
        help=f"download mode: {', '.join(sorted(selector.MODES))} (default: best)")
    parser.add_argument(
        "-f", "--links", dest="link_file", default=None, metavar="PATH",
        help="queue file to read instead of links.txt")
    parser.add_argument(
        "-r", "--resolution", dest="max_height", type=int, default=None,
        metavar="HEIGHT",
        help="cap video height, e.g. 720 or 1080 (default: best available)")
    parser.add_argument(
        "-o", "--output", dest="output_dir", default=None, metavar="DIR",
        help="output folder (default: downloads/)")
    parser.add_argument(
        "--numbered", dest="numbered", action="store_true", default=True,
        help="prefix outputs with their queue position (default)")
    parser.add_argument(
        "--no-numbered", dest="numbered", action="store_false",
        help="do not prefix output filenames with a number")
    parser.add_argument(
        "--list-formats", action="store_true",
        help="show available formats for each URL and exit (no download)")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="show what would be selected for each URL, then exit")
    parser.add_argument(
        "--keep-intermediates", action="store_true",
        help="leave downloaded streams in video/ and audio/ instead of recycle/")
    parser.add_argument(
        "--no-archive", action="store_true",
        help="ignore the download archive and re-download completed items")
    parser.add_argument(
        "--cookies-from-browser", default=None, metavar="BROWSER",
        help="load cookies from BROWSER (chrome, firefox, edge, ...) for "
             "age-gated or bot-checked videos")
    parser.add_argument(
        "--cleanup", action="store_true",
        help="prune recycle/ and remove leftover .part files, then exit")
    parser.add_argument(
        "--gui", action="store_true",
        help="open the desktop window instead of running in the terminal")
    parser.add_argument(
        "-q", "--quiet", action="store_true",
        help="only print errors and the final summary")
    parser.add_argument(
        "-V", "--version", action="version",
        version=f"YT-Duo {__version__}")
    return parser


# ── Queue reading ────────────────────────────────────────────────────

def read_queue(path):
    """Read URLs from a queue file.

    Fixes the old parser, which handed every non-empty line to yt-dlp --
    including notes, separators, and (historically) API keys. Only real
    URLs are accepted; ``#`` starts a comment; ``---end---`` stops reading.
    Duplicates are dropped, and the first URL found inside an otherwise
    non-URL line is salvaged (the file has lines like "Name - https://...").
    """
    urls, seen = [], set()
    if not os.path.exists(path):
        return urls

    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            if line.lower() == "---end---":
                break
            if line.startswith("#"):
                continue

            found = extract_urls(line)
            if not found and not line.lower().startswith(("http://", "https://")):
                continue  # notes, separators, leaderboards, credentials

            for url in found:
                if url not in seen:
                    seen.add(url)
                    urls.append(url)
    return urls


def prompt_for_urls(stream=None):
    """Interactive fallback. EOF-safe: returns what it got instead of crashing."""
    stream = stream or sys.stdout
    print("  No URLs provided and no queue file found.", file=stream)
    print("  Paste URLs (one per line, blank line to start):\n", file=stream)
    urls = []
    while True:
        try:
            line = input("  > ").strip()
        except EOFError:
            break
        if not line:
            break
        found = extract_urls(line)
        if found:
            urls.extend(found)
    return urls


# ── Console rendering ────────────────────────────────────────────────

class ConsoleRenderer:
    """Turn engine events into terminal output."""

    def __init__(self, quiet=False, stream=None):
        self.quiet = quiet
        self.stream = stream or sys.stdout
        self.last_was_progress = False

    def _w(self, text):
        self.stream.write(text)
        self.stream.flush()

    def _newline_if_needed(self):
        if self.last_was_progress:
            self._w("\n")
            self.last_was_progress = False

    def __call__(self, name, data):
        handler = getattr(self, f"_on_{name}", None)
        if handler:
            handler(data)

    def _on_item_start(self, d):
        self._newline_if_needed()
        if not self.quiet:
            self._w(f"  [{d['index']}/{d['total']}] {d['url']}\n")

    def _on_probe(self, d):
        if self.quiet:
            return
        self._w(f"        Title  {d['title'][:70]}\n")
        self._w(f"        Video  {d['video']}\n")
        if d["audio"]:
            self._w(f"        Audio  {d['audio']}\n")

    def _on_progress(self, d):
        if self.quiet:
            return
        parts = [f"{d['label']}  {d['percent']}"]
        if d["speed"]:
            parts.append(f"at {d['speed']}")
        if d["eta"]:
            parts.append(f"ETA {d['eta']}")
        self._w(f"\r        {'  '.join(parts):<64}")
        self.last_was_progress = True

    def _on_stream_done(self, d):
        self._newline_if_needed()
        if not self.quiet:
            self._w(f"        {d['label']}  {d['size']}  done\n")

    def _on_retry(self, d):
        self._newline_if_needed()
        if not self.quiet:
            self._w(f"        {d['label']} interrupted, retry {d['attempt']}"
                    f"/{d['total'] - 1} in {d['wait']}s\n")

    def _on_warning(self, d):
        self._newline_if_needed()
        if not self.quiet:
            self._w(f"        Warn: {d['message']}\n")

    def _on_item_ok(self, d):
        self._newline_if_needed()
        if not self.quiet:
            r = d["report"]
            self._w(f"        OK  {r['resolution']} @ {r['fps']}fps"
                    f"  {r['video_codec']}/{r['audio_codec']}"
                    f"  {r['duration']}s\n\n")

    def _on_item_failed(self, d):
        self._newline_if_needed()
        self._w(f"        FAILED  {d['reason']}\n\n")

    def _on_item_skipped(self, d):
        self._newline_if_needed()
        if not self.quiet:
            self._w(f"  [{d['index']}] already downloaded, skipped\n")

    def _on_recycle_pruned(self, d):
        if not self.quiet:
            self._w(f"  Recycle: pruned {d['removed']} file(s), freed {d['freed']}\n")

    def _on_orphans_removed(self, d):
        if not self.quiet:
            self._w(f"  Orphans: removed {d['removed']} partial file(s), freed {d['freed']}\n")

    def _on_batch_interrupted(self, d):
        self._newline_if_needed()
        self._w("\n  Interrupted. Downloaded streams were kept in recycle/.\n")

    def _on_batch_done(self, d):
        self._newline_if_needed()
        self._w(f"  Done: {d['completed']} completed, {d['failed']} failed")
        if d["skipped"]:
            self._w(f", {d['skipped']} skipped")
        self._w("\n")
        if d["failed"]:
            self._w("\n  Failures:\n")
            for item in d["results"]:
                if not item["ok"]:
                    self._w(f"    - {item['url']}\n      {item['reason']}\n")


# ── Pause (EOF-safe) ─────────────────────────────────────────────────

def pause(message="\n  Press Enter to exit..."):
    """Never let a closed stdin turn a clean exit into a traceback."""
    try:
        input(message)
    except (EOFError, KeyboardInterrupt):
        pass


# ── Entry point ──────────────────────────────────────────────────────

def _print_formats(url, settings, out):
    logger = engine.EngineLogger()
    info = engine.probe(url, settings, logger)
    out.write(f"\n  {info.get('title', info.get('id'))}\n")
    out.write(f"  {'id':>8}  {'ext':<5} {'resolution':<12} {'fps':>5} "
              f"{'codec':<10} {'audio':<10} {'bitrate':>9}\n")
    for fmt in info.get("formats") or []:
        out.write(
            f"  {str(fmt.get('format_id')):>8}  {str(fmt.get('ext')):<5} "
            f"{str(fmt.get('resolution')):<12} "
            f"{(selector.fps_of(fmt)):>5.0f} "
            f"{str(selector.codec_family(selector.vcodec_of(fmt)) or '-'):<10} "
            f"{str(selector.codec_family(selector.acodec_of(fmt)) or '-'):<10} "
            f"{selector.bitrate_of(fmt):>8.0f}k\n")
    return info


def main(argv=None):  # noqa: C901 - a CLI entry point is linear by nature
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.gui:
        from .gui import main as gui_main
        return gui_main()

    out = sys.stdout
    file_config = read_config_file(Settings().config_file)
    mode = args.mode or resolve_mode(None, file_config)
    if mode not in selector.MODES:
        parser.error(f"invalid mode {mode!r} from config (choose from "
                     f"{', '.join(sorted(selector.MODES))})")

    settings = Settings(
        mode=mode,
        link_file=args.link_file or "links.txt",
        output_dir=args.output_dir,
        use_archive=not args.no_archive,
        cookies_from_browser=args.cookies_from_browser,
        max_height=args.max_height,
    )

    renderer = ConsoleRenderer(quiet=args.quiet, stream=out)

    # Startup hygiene: prune the recycle cache and sweep orphaned partials.
    if args.cleanup:
        engine.prune_recycle(settings, renderer)
        engine.sweep_orphans(settings, renderer)
        out.write("  Cleanup complete.\n")
        return 0

    ffmpeg_path = engine.find_ffmpeg(settings)
    ffprobe_path = engine.find_ffprobe(settings)
    if not ffmpeg_path or not ffprobe_path:
        out.write("  ERROR: ffmpeg/ffprobe not found.\n")
        out.write("  Run runner.bat to install all dependencies.\n")
        pause()
        return 2

    urls = list(args.urls)
    if not urls:
        if args.link_file and not os.path.exists(settings.link_file):
            # An explicitly named queue file that does not exist is an error,
            # not a cue to prompt: a scheduled job must not sit waiting.
            out.write(f"  ERROR: queue file not found: {settings.link_file}\n")
            return 1
        urls = read_queue(settings.link_file)
    if not urls:
        urls = prompt_for_urls(out)

    if not urls:
        out.write("  No links provided.\n")
        pause()
        return 1

    out.write("\n  YT-Duo v" + __version__ + " - Downloading\n")
    out.write("  ============================\n\n")
    out.write(f"  Mode: {mode}   Links: {len(urls)}\n\n")

    if args.list_formats:
        for url in urls:
            try:
                _print_formats(url, settings, out)
            except Exception as exc:
                out.write(f"\n  FAILED  {url}\n      {engine.classify_error(exc)}\n")
        return 0

    if args.dry_run:
        for index, url in enumerate(urls, 1):
            try:
                if not engine.is_host_supported(url):
                    raise engine.UnsupportedHostError(
                        f"no extractor for this host: {url}")
                info = engine.probe(url, settings)
                video, audio, _ = selector.pick_streams(
                    info.get("formats") or [], mode, settings.max_height)
                container = engine._container_for_streams(video, audio)
                out.write(f"  [{index}] {info.get('title', url)[:60]}\n")
                out.write(f"       {selector.describe(video)} -> .{container}\n")
                if audio:
                    out.write(f"       {selector.describe(audio, True)}\n")
            except Exception as exc:
                out.write(f"  [{index}] {url}\n       FAILED  {engine.classify_error(exc)}\n")
        return 0

    # Runtime hygiene before downloading.
    engine.prune_recycle(settings, renderer)
    engine.sweep_orphans(settings, renderer)

    status = 0
    try:
        summary = engine.run_batch(urls, settings, emit=renderer)
        status = 0 if summary["failed"] == 0 else 1
    except KeyboardInterrupt:
        out.write("\n  Interrupted.\n")
        status = 130
    except Exception as exc:  # noqa: BLE001 - top-level guard
        out.write(f"\n  ERROR: {engine.classify_error(exc)}\n")
        status = 1

    if sys.stdin is not None and sys.stdin.isatty():
        pause()
    return status


if __name__ == "__main__":
    sys.exit(main())
