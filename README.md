# DownY

A batch video downloader built on [yt-dlp](https://github.com/yt-dlp/yt-dlp).
Drop URLs in a file, run one command, get merged video files out.

Designed for **bulk** work: it keeps going when one item fails, tells you
which ones failed and why, and never silently overwrites a file.

---

## Quick start

```bat
runner.bat
```

`runner.bat` installs anything missing (Python, pip, ffmpeg, deno, yt-dlp),
then runs the downloader. Run it again if it asks you to.

Or, if your dependencies are already installed:

```bat
python -m downy                     :: read links.txt
python -m downy -m compat           :: H.264/AAC in MP4
python -m downy --dry-run           :: preview selections, download nothing
python -m downy https://youtu.be/ID :: one URL, no queue file
```

---

## The queue file — `links.txt`

One URL per line. Everything else is ignored:

- blank lines
- lines starting with `#`
- lines with no URL in them (notes, separators, leaderboards)
- everything after a `---end---` line

Duplicates are dropped. A line like `Some Tool - https://example.com/` still
contributes its URL. **Never put API keys or passwords in this file** — it is
git-ignored, but it is plain text.

---

## Modes

The mode decides how streams are ranked. It is a policy, not a resolution cap:
DownY always takes the best tier the video actually offers.

| Mode | Video | Audio | Container |
|---|---|---|---|
| `best` (default) | highest resolution, then fps, then bitrate | best audio | MKV |
| `efficient` | highest resolution, preferring AV1 over VP9 | best audio | MKV |
| `compat` | H.264 first — plays anywhere | AAC | MP4 |

The container is chosen from the streams that were **actually** muxed, not from
the mode, so you never get an MP4 containing Opus audio.

Set the mode with any of, in precedence order:

1. `--mode` / `-m` on the command line
2. `"mode"` in `config.json`
3. the `DOWNY_MODE` environment variable

---

## Commands

```
python -m downy [URL ...] [options]

-m, --mode MODE              best | efficient | compat
-f, --links PATH             queue file to read instead of links.txt
-o, --output DIR             output folder (default: downloads/)
    --no-numbered            don't prefix filenames with a queue number
    --dry-run                show what would be picked, then exit
    --list-formats           print every available stream, then exit
    --cookies-from-browser   e.g. chrome, firefox, edge (bot checks, age gates)
    --no-archive             re-download items already in the archive
    --cleanup                prune recycle/ and remove leftover .part files
-q, --quiet                  errors and summary only
-V, --version
```

Every run also prunes the recycle cache and sweeps orphaned `.part` files
before starting.

---

## What happens to a download

```
probe ──> pick_streams ──> download video ──> download audio ──> merge ──> verify
                                                                     │
                                                  ffmpeg stream copy │
                                                  ffprobe confirms   │
                                                                     ▼
                                                             downloads/
                                          intermediates ──> recycle/
```

- Streams are **copied**, never re-encoded, so quality is untouched.
- Every output is **verified with ffprobe** against the streams that were
  selected — wrong height, fps, or codec is caught immediately rather than
  discovered later.
- On failure the partial output is deleted; the source streams stay in
  `recycle/` so a rerun resumes cheaply.
- Failed items never renumber the successes: item 3 failing leaves `03.`
  missing, not shuffled.

### Output naming

`01. Title.mkv` — zero-padded queue number plus a sanitised title. The
sanitiser strips Windows-reserved characters, control characters, reserved
device names (`CON`, `NUL`, `COM1`…), trailing dots and spaces, and caps length
at 120 characters (Unicode titles are preserved). If a name already exists,
` (2)`, ` (3)`… is appended. **Nothing is ever overwritten.**

### Folders

| Folder | Purpose |
|---|---|
| `downloads/` | finished files |
| `video/`, `audio/` | in-progress streams |
| `recycle/` | intermediates kept for resuming — a **cache**, pruned by age (72 h) and size (4 GB) |

`.downy-archive.txt` records completed videos so re-runs skip them
(`--no-archive` disables this).

---

## Skipped or failed?

| Message | Meaning |
|---|---|
| `host not supported by yt-dlp` | DownY has no extractor for this site. Fails instantly, no network |
| `YouTube bot check - try --cookies-from-browser` | Add `--cookies-from-browser chrome` |
| `rate limited` | Use a larger gap; reduce the queue size |
| `no usable stream for the selected mode` | Try another `--mode` |
| `ffmpeg failed` | See the message tail; often a corrupt or interrupted source |

Exit codes: `0` all succeeded, `1` some failed, `2` ffmpeg/ffprobe missing,
`130` interrupted.

---

## Development

```bat
python -m unittest test_selector test_engine -v   :: 121 offline tests
python -m unittest test_engine -v                 :: engine only
python smoke_test.py                              :: live network test
python capture_fixtures.py                        :: refresh format fixtures
```

`test_selector` and `test_engine` are fully offline — no network, no ffmpeg,
no real folders touched. `smoke_test.py` is the only one that downloads.

### Layout

```
downy/
  config.py    Settings: paths, tunables, mode resolution
  selector.py  Pure format ranking (no I/O, no network)
  naming.py    Filesystem-safe names, collision-free paths
  engine.py    The pipeline (UI-agnostic, event callbacks)
  cli.py       Terminal front-end
ytd.py         Backward-compatible shim -> python -m downy
```

`engine.py` never prints, never prompts, and never reads `sys.argv`. It reports
progress through an `emit(name, payload)` callback:

```python
from downy.config import Settings
from downy import engine

settings = Settings(mode="compat")
summary = engine.run_batch(urls, settings,
                           emit=lambda name, data: print(name, data),
                           should_stop=lambda: False)
```

That seam is why the planned GUI and web front-ends need no knowledge of
yt-dlp or ffmpeg.

### Roadmap

- **GUI** (`downy.gui`) — a small desktop window: drop URLs, pick a mode, watch
  the same events the CLI renders.
- **Web** (`downy.web`) — paste a link in a browser and get the file. The
  engine is already front-end agnostic; this needs a job queue and a streaming
  response, not a rewrite.

---

## Requirements

Python 3.10+, ffmpeg + ffprobe on `PATH` (or next to the project), and
yt-dlp (`pip install -r requirements.txt`). deno is recommended so yt-dlp can
solve YouTube's JS challenges.

