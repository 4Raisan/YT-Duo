# YT-Duo

**Download YouTube videos in the best available quality.**

A local desktop and command-line video downloader powered by [yt-dlp](https://github.com/yt-dlp/yt-dlp). Queue links, choose quality, and save verified files. FFmpeg combines video and audio without re-encoding.

Project release date: **October 18, 2026** · Version **2.1.0**

## Features

- Dark/light desktop themes, progress, activity log, and cancellation.
- Add links to a running queue.
- Best quality, smaller-file, and compatibility selection policies.
- Resolution preferences, Windows-safe filenames, and collision suffixes.
- Batch processing continues after individual failures.
- FFprobe output checks and a completion archive to skip existing downloads.
- Optional browser cookies for authenticated content.

## Windows quick start

```powershell
git clone https://github.com/4Raisan/YT-Duo.git
cd YT-Duo
.\runner.bat
```

`runner.bat` checks dependencies and can install missing Python, pip, FFmpeg, Deno, and yt-dlp through winget/pip. Setup needs internet access and can change your Python environment. If PATH changes, reopen the terminal and run it again.

With dependencies installed, double-click **YT-Duo.bat** for the GUI without a persistent console. This lightweight launcher checks Tkinter and attempts to install yt-dlp if absent; use `runner.bat` for full setup.

## Manual setup

Requires **Python 3.10+**, **Tkinter** for the GUI, and **ffmpeg / ffprobe** on PATH. Deno is recommended for YouTube JavaScript challenges. Windows is the primary development platform.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m downy --gui
```

The internal package remains `downy` for compatibility with the original DownY project; the public app name is **YT-Duo**.

## Command line

```powershell
python -m downy https://www.youtube.com/watch?v=VIDEO_ID
python -m downy -f links.txt
python -m downy -m compat -r 1080
python -m downy --dry-run https://www.youtube.com/watch?v=VIDEO_ID
python -m downy --list-formats https://www.youtube.com/watch?v=VIDEO_ID
python -m downy --cookies-from-browser firefox
python -m downy --help
```

Copy `links.example.txt` to `links.txt` and add your URLs. Queue files support blank lines, `#` comments, embedded URLs, duplicate removal, and a `---end---` stop marker. Your actual queue is ignored by Git.

| Mode | Selection policy |
|---|---|
| `best` | Resolution, frame rate, then video bitrate |
| `efficient` | Resolution and frame rate, then AV1/VP9 codec preference |
| `compat` | H.264 video and AAC audio preferred |

Policies rank available streams; they do not transcode. MP4 or MKV is chosen from selected codecs. A resolution preference can be exceeded if no candidate meets the cap.

CLI mode precedence: explicit flag → `config.json` → `DOWNY_MODE` → `best`. Other GUI preferences are not all loaded by the CLI; pass CLI flags explicitly.

## Storage and dependencies

| Location | Contents |
|---|---|
| `downloads/` | Finished files and `.downy-archive.txt` |
| `video/`, `audio/` | Input streams and partial transfers |
| `recycle/` | Completed intermediates after successful merging |
| `config.json` | Local GUI preferences |

Paths default to the project directory. No hosted backend, database service, or API key is required. yt-dlp accesses source sites directly; cookies are optional. Metadata is extracted during probing and again for each stream download; retries add requests. Merging needs disk space for both input streams and output.

The CLI prunes recycle to 72 hours / 4 GiB before downloading and removes leftover partial files. The GUI currently skips startup cleanup. Run `python -m downy --cleanup` only while no download is active.

## Current limitations

- CLI partial-file cleanup prevents guaranteed cross-run resume.
- Cancellation is cooperative; probes, retry waits, and FFmpeg can delay stopping.
- `--no-numbered` is accepted but currently not applied.
- No playlist expansion, subtitles, or web interface.
- Generic-only extractors are rejected by default.
- Concurrent app instances sharing work folders are not coordinated.

## Development

```powershell
python -m unittest test_selector test_engine test_gui -v
```

Tests use offline fixtures and mocked media operations. GUI tests use real Tk windows and skip without a display. `smoke_test.py` downloads live media; `capture_fixtures.py` fetches live metadata. Run these separately and deliberately.

See [architecture](docs/ARCHITECTURE.md) and [changelog](CHANGELOG.md).

YT-Duo is independent and not affiliated with YouTube or Google. Download content you have permission to save.
