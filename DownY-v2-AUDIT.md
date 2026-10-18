# DownY v2 — Project Audit

**Path:** `C:\Users\4Raisan\Desktop\MIII\Personal Projects\DownY v2`
**Date:** 2026-09-26
**Audit type:** Full read of source + live empirical testing against the URLs in your own `links.txt`

---

## 1. Executive summary

DownY v2 is a small, well-organised batch video downloader. The architecture is sound and the code is
readable — but **it does not currently work on most of its own input list.**

I tested the actual pipeline against the real URLs in your `links.txt`:

| Input | Result |
|---|---|
| 5 YouTube links | **2 succeed, 3 fail** (`requested format is not available`) |
| 4 non-YouTube links | **4 fail** (`Unsupported URL` / `not a valid URL`) |
| `links.txt` parsing | **190 "links" extracted, only 66 are real URLs → 124 guaranteed failures** |

The headline bug is the format selector on `ytd.py:88`. It hardcodes
`bestvideo[height=1080][vcodec^=avc1]` with **no fallback**, so any video without a 1080p H.264
stream dies immediately. Three of your five YouTube links have no 1080p H.264 stream at all.

**Bottom line:** the scaffolding is good; the format selection, input validation, and error handling
need work. Roughly a dozen targeted fixes would make this genuinely reliable.

---

## 2. What the project is

Three files, no dependencies beyond `yt-dlp` + `ffmpeg` + Python:

| File | Role |
|---|---|
| `runner.bat` | Bootstrapper. Installs/checks Python, pip, ffmpeg, Deno, yt-dlp, then launches the downloader. |
| `ytd.py` | The downloader (232 lines). Reads URLs, downloads video+audio separately, merges to MP4. |
| `links.txt` | The input queue (283 lines). Also doubles as a personal notes/scratchpad file. |
| `__pycache__/` | Stale bytecode from a previous import. Not needed. |

No git repo, no `requirements.txt`, no `.gitignore`, no README, no config file, no tests.

---

## 3. How it works right now

### 3.1 Startup — `runner.bat`

```
winget present?  --no--> stop
python present?  --no--> winget install Python.Python.3.12 (silent)   --+
pip present?     --no--> python -m ensurepip --upgrade                | set NEED_RESTART=1
ffmpeg present?  --no--> winget install Gyan.FFmpeg (silent)          |
deno present?    --no--> winget install DenoLand.Deno (silent)       --+
        |
        +- NEED_RESTART=1 ? --yes--> "close window, run again" and exit
        |
pip install --upgrade yt-dlp
cls, 3s timeout
python ytd.py
pause
```

### 3.2 Import side effect — `ytd.py:16-17`

```python
for folder in [VIDEO_DIR, AUDIO_DIR, OUTPUT_DIR, RECYCLE_DIR]:
    os.makedirs(folder, exist_ok=True)
```

This runs **at module level**, so merely importing `ytd` creates `video/`, `audio/`, `downloads/`,
`recycle/` in the current working directory. (Evidence: `__pycache__/ytd.cpython-314.pyc` exists —
the module has been imported at some point — yet none of those four folders exist now.)

### 3.3 Input parsing — `get_links()` (`ytd.py:159-184`)

- If `links.txt` exists, take every non-empty line, stopping at a literal `---end---`.
- If the file is missing/empty, fall back to an interactive `input()` prompt.
- **No URL validation. No dedupe. No comment support. No playlist handling.**

### 3.4 Per-link pipeline — `main()` (`ytd.py:215-225`)

For each URL, numbered `[i/total]`:

1. `download_video(url)` → `bestvideo[height=1080][vcodec^=avc1]` → `video/{id}_video.{ext}`
2. `download_audio(url, vid)` → `bestaudio[ext=m4a]/bestaudio` → `audio/{id}_audio.{ext}`
3. `merge(i, title, video, audio, ffmpeg)`:
   - Sanitise title → `downloads/{i}. {title}.mp4`
   - `ffmpeg -y -i vid -i aud -c:v copy -c:a aac out.mp4`
   - `shutil.move()` both inputs into `recycle/`
4. Any exception → print `FAILED {e}`, increment counter, continue.

### 3.5 Progress display — `make_hook()` (`ytd.py:65-81`)

A custom yt-dlp `progress_hooks` writes a single `\r`-updated line per stream
(`Video  43.2%  at 12.4MiB/s  ETA 00:31`), plus a `done` line with total size.
`SilentLogger` + `quiet` + `no_warnings` suppress everything else.

---

## 4. Verified environment

| Component | Version / status |
|---|---|
| Python | 3.14.3 |
| yt-dlp | 2026.08.19 (imports as `yt_dlp`) |
| ffmpeg | 8.1.1 (WinGet) — also a 214 MB `ffmpeg.exe` in `%USERPROFILE%` |
| deno | present via WinGet |
| `ytd.py` | parses cleanly, 232 lines |

---

## 5. Bugs

### 🔴 B1 — Format selector has no fallback (breaks 60% of your YouTube list)

**`ytd.py:88`**

```python
"format": "bestvideo[height=1080][vcodec^=avc1]",
```

A single selector with no `/` fallback. yt-dlp raises
`DownloadError: requested format is not available` when nothing matches.

**Empirically verified against your links.txt:**

| Video | Max height | avc1 heights available | Current selector |
|---|---|---|---|
| `qrIqJO1IiuM` | 720 | 144…720 | ❌ **FAIL** |
| `8qZELvy3-LY` | 1920 | 128…960 | ❌ **FAIL** |
| `GczjVOpaPPI` | 720 | 144…720 | ❌ **FAIL** |
| `X98-gKKR4Bk` | 1080 | 144…1080 | ✅ ok |
| `4rWx4cBh684` | 1080 | 144…1080 | ✅ ok |

Note `8qZELvy3-LY` *has* 1920p — but only as AV1/VP9. Strict `vcodec^=avc1` rejects it and then
throws away the 1080p/720p H.264 options too, because there is no fallback branch.

**Fix:**
```python
"format": "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/"
          "bv*[height<=1080]+ba/b[height<=1080]/b",
"merge_output_format": "mp4",
```

---

### 🔴 B2 — `links.txt` yields 124 garbage "links"

**`ytd.py:159-172`**

The `---end---` sentinel is **not present anywhere in your `links.txt`**, so the parser consumes the
entire file. Measured result:

- 283 total lines → 190 non-empty → treated as 190 links
- Only **66** begin with `http`
- **124 lines are not URLs** and are each handed to yt-dlp anyway

Examples of what gets submitted as a "link":
- `fe_oa_aa835ce9a13d61fa9e7cb7ff86289e3f98bb3b74a7ce02b8` → `[generic] ... is not a valid URL`
- `Pomeli AI - https://labs.google.com/pomelli/about/`
- `🥇 GLM-5.2 10.0`
- `=====================================================================`
- ~15 lines containing API keys

Every one is a network round-trip and a guaranteed failure. On a slow link this is minutes of wasted
time before the real work starts.

**Fix:** validate + filter in `get_links()`:
```python
if not item.lower().startswith(("http://", "https://")):
    continue
```
plus support `#` comments, `---end---`, and dedupe via `dict.fromkeys()`.

---

### 🔴 B3 — `find_file()` can return a partial or stale file

**`ytd.py:51-55`**

```python
pattern = os.path.join(directory, f"{video_id}_{suffix}.*")
files = glob.glob(pattern)
return files[0] if files else None
```

Two problems, both **verified by simulation**:

1. `*` matches everything, including yt-dlp's in-progress artefacts. With `abc123_video.mp4`,
   `abc123_video.mp4.part`, `abc123_video.mp4.ytdl`, `abc123_video.webm` and
   `abc123_video.f137.mp4` all present, glob returns **5 matches** and `files[0]` handed back
   `abc123_video.f137.mp4`.
2. `glob` order is filesystem-dependent and unordered. If an earlier run left a `.webm` and this run
   produces `.mp4`, you may merge **the old file**. There is no timestamp or size check.

An interrupted download therefore feeds a truncated `.part` straight into ffmpeg.

**Fix:** use yt-dlp's own answer instead of guessing:
```python
def find_file(directory, video_id, suffix, info=None):
    for d in (info or {}).get("requested_downloads") or []:
        p = d.get("filepath") or d.get("_filename")
        if p and os.path.isfile(p) and not p.endswith((".part", ".ytdl")):
            return p
    matches = [f for f in glob.glob(os.path.join(directory, f"{video_id}_{suffix}.*"))
               if not f.endswith((".part", ".ytdl", ".temp"))]
    return max(matches, key=os.path.getmtime) if matches else None
```

---


### 🟠 B4 — Unguarded `input()` crashes in any non-interactive context

**`ytd.py:199, 205, 228`** — verified: `input()` on non-TTY stdin raises `EOFError`.

The script dies with a traceback if run from a Task Scheduler job, a CI step, a piped command, or
`python ytd.py < nul`. The traceback is the *only* error report, and the final
`Done: N completed, M failed` summary is never printed.

**Fix:** wrap in a helper:
```python
def pause():
    try:
        input("\n  Press Enter to exit...")
    except EOFError:
        pass
```

---

### 🟠 B5 — `runner.bat` never changes directory, so all paths are CWD-relative

**`runner.bat:95`**

```bat
python "%~dp0ytd.py"
```

The script path is absolute, but `ytd.py` uses bare relative paths (`links.txt`, `downloads/`, …).
Combined with the import-time `os.makedirs` (`ytd.py:16-17`), launching from anywhere other than the
project folder creates `video/ audio/ downloads/ recycle/` **in the wrong place** and finds no
`links.txt`, silently falling back to interactive mode.

**Fix:**
```bat
cd /d "%~dp0"
python ytd.py
```
and/or resolve paths from `os.path.dirname(os.path.abspath(__file__))` in Python.

---

### 🟠 B6 — ffmpeg errors are completely swallowed

**`ytd.py:146-150`**

```python
subprocess.run([ffmpeg_path, "-y", "-i", video_file, "-i", audio_file,
                "-c:v", "copy", "-c:a", "aac", out],
               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
```

`check=True` + `stderr=DEVNULL` produces exactly this user-facing message:

> `FAILED Command '['C:\\...\\ffmpeg.EXE', '-y', ...]' returned non-zero exit code 1.`

No indication whether it was a codec mismatch, a corrupt input, a full disk, or a path-length
problem. ffmpeg also gets no `-loglevel error`, no `-map` to pin streams, and no
`-movflags +faststart` for web playback.

**Fix:**
```python
p = subprocess.run([..., "-loglevel", "error", "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", out],
                   capture_output=True, text=True)
if p.returncode != 0:
    raise RuntimeError(f"ffmpeg failed: {p.stderr.strip()[-300:]}")
```

---

### 🟠 B7 — Filename sanitiser produces invalid Windows filenames

**`ytd.py:140`**

```python
safe = "".join(c for c in title if c not in r'\/:*?"<>|')
```

Verified failure modes:

| Input title | Result | Problem |
|---|---|---|
| `CON` / `nul` | `CON` | ❌ Windows reserved device name — write fails or hits the console |
| `ends with dot.` | `ends with dot.` | ❌ trailing dot stripped by Win32 |
| `Trailing space ` | `Trailing space ` | ❌ trailing space stripped by Win32 |
| `   ` (whitespace) | `   ` | ❌ resolves to a name that is only the `.mp4` ext |
| `Episode 1 \| Part 2\r\nnewline` | contains `\r\n` | ❌ control chars in filename |
| 200-char title | 200 chars | ⚠️ `downloads/1. ` + 200 → over MAX_PATH (260) |
| `a/b:c*d?e"f<g>h\|i` | `abcdefghi` | ✅ correct |

**Fix:** add control-char stripping, `.strip()`, a reserved-name guard, trailing-dot removal, and a
length cap (~120 chars).

---


### 🟠 B8 — `runner.bat` reports success even when installation fails

**`runner.bat:86-87`**

```bat
python -m pip install --upgrade yt-dlp --quiet >nul 2>&1
echo   [OK] yt-dlp
```

`errorlevel` is never checked. A failed install (offline, proxy, permission) still prints `[OK]`.
The same pattern appears at lines 27, 37, 53 and 67, where `echo [+] ... installed` runs
unconditionally after the `winget` call.

---

### 🟠 B9 — Failed downloads leak files forever

**`ytd.py:217-224`**

`recycle()` is only called **after** a successful merge. If audio fails after video succeeded, or if
ffmpeg fails, the `.mp4`/`.m4a` stay in `video/` and `audio/` indefinitely. There is no cleanup in
the `except` block and no periodic sweep. On a 60-link run, disk usage grows without bound.

**Fix:** track paths outside the `try` and clean up in a `finally:`.

---

### 🟠 B10 — `recycle/` doubles your disk usage permanently

**`ytd.py:126-136`** uses `shutil.move`, so every intermediate file is *kept*, not deleted. A 2 GB
video ends up occupying 2 GB in `recycle/` forever, and the folder is never pruned. Peak usage during
a merge is ~2× the final size.

**Fix:** delete after a successful merge, or point `recycle/` at `%TEMP%`.

---

### 🟡 B11 — ANSI regex only strips one escape type

**`ytd.py:24`**

```python
re.sub(r"\x1b\[[0-9;]*m", "", str(text))
```

The terminator is hardcoded to `m` (colour). yt-dlp also emits `\x1b[K`, `\x1b[2K` and `\x1b[G`
(cursor/erase sequences), which survive and corrupt the single-line progress display.
Use `r"\x1b\[[0-9;?]*[a-zA-Z]"`.

---

### 🟡 B12 — `format_size()` has no KB or TB tier

**`ytd.py:27-31`** — verified:

| Bytes | Shown | Should be |
|---|---|---|
| `1024` | `0.0 MB` | `1.0 KB` |
| `2199023255552` (2 TiB) | `2048.0 GB` | `2.0 TB` |
| `0` / `None` | `? MB` | fine, but this is the **common** case — see B18 |

---

### 🟡 B13 — Bot checks and warnings are silent failures

`SilentLogger` (`ytd.py:58-62`) discards **all** `warning` output, and `no_warnings: True` is set.
The most common real-world YouTube failure in 2026 is
`Sign in to confirm you're not a bot. Use --cookies-from-browser`. That surfaces as a generic
exception, and the very warning that would explain it is thrown away.

There is also **no cookie support at all** — no `cookiesfrombrowser`, no `cookies.txt`. Age-gated,
private, and members-only videos are unreachable.

Related: `deno` is installed by `runner.bat` for yt-dlp's JS challenge solving, but if yt-dlp can't
find the runtime it only *warns* — and B13 suppresses that warning. You get silent failures.

---


### 🟡 B14 — Every video is extracted twice

**`ytd.py:97-98` then `ytd.py:118-119`**

`download_video` calls `ydl.extract_info(url, download=True)` and discards the info dict; then
`download_audio` opens a new `YoutubeDL` and calls `extract_info` on the **same URL** again. That's a
duplicate metadata/player round-trip per link — more latency and a second chance to hit rate limiting.
Pass the already-extracted `info` through instead.

---

### 🟡 B15 — Output numbering leaves gaps

**`ytd.py:141`** — `out = f"{index}. {safe}.mp4"` where `index` is the position in the input list.
If link 3 of 10 fails, `downloads/` contains `1.`, `2.`, `4.`, `5.`… For a series that's a broken
sequence. Consider numbering only successes, or keeping a separate success counter.

---

### 🟡 B16 — Non-YouTube sources cannot work with this design

**Verified — all 4 non-YouTube links in your `links.txt` fail:**

| URL | Result |
|---|---|
| `https://streamtape.com/v/XPeKXgJOqXuzzO/Lovers.mp4` | `Unsupported URL` |
| `https://firestream.to/e/6bkEbwQm` | `Unsupported URL` (redirects to `.site`) |
| `https://cinesubz.lk/tvshows/money-heist-2021-sinhala-sub/` | `Unsupported URL` (redirects to `.co`) |
| `fe_oa_aa835…` (not a URL) | `[generic] ... is not a valid URL` |

Even for supported generic sources, the `bestvideo` / `bestaudio` two-pass design breaks: the generic
extractor exposes a **single muxed** format, so `bestaudio[ext=m4a]` matches nothing and there is no
audio-only stream to fetch. A `bv*+ba/b` selector with `merge_output_format` is required.

---

### 🟡 B17 — No dependency pinning; installs into the global Python

**`runner.bat:86`** upgrades `yt-dlp` into system site-packages on every run. yt-dlp publishes
frequently and has shipped breaking changes; an unpinned auto-upgrade can break `ytd.py` overnight
with no way to pin a known-good version. There's no `requirements.txt` and no virtualenv, so this
also risks conflicting with your other Python projects.

---

### 🟡 B18 — `format_size(None)` on most "done" lines

**`ytd.py:78-79`** — `total_bytes`/`total_bytes_estimate` are frequently absent. The result is that
`Audio  ? MB  done` appears often enough to look broken.

---

### 🟡 B19 — No resume, no skip-completed, no rate limiting

Every run re-downloads the entire queue from scratch. `download_archive` is not set, so there's no
record of what's already in `downloads/`. There is also no `sleep_interval` between links, so a large
queue is a good way to earn a rate-limit ban.

---

### 🟡 B20 — Fragile `python` detection

**`runner.bat:22-30`** — on Windows, `python` may resolve to the Microsoft Store app-execution alias,
which can print a "Python was not found" message while still returning exit code 0. `py -3` is the more
reliable launcher on Windows. Also, `python --version` prints to **stderr**; the `2^>^&1` on line 29 is
correct, but line 22's `>nul 2>&1` hides genuine errors too.

---


## 6. Verified as **correct** (no action needed)

Recording these so they don't get "fixed" later:

- ✅ `winget install … -h` — `-h` **is** the documented short form for `--silent` in `winget install`.
  Not a bug. (Confirmed against Microsoft's `winget install` docs.)
- ✅ `if "%NEED_RESTART%"=="1"` on `runner.bat:71` reads the variable *outside* the `if`-block, so
  delayed expansion is not required. The `set` inside the block does take effect.
- ✅ The title filter string `r'\/:*?"<>|'` does correctly include the backslash.
- ✅ `"bestaudio[ext=m4a]/bestaudio"` (`ytd.py:109`) *does* have a proper fallback.
- ✅ `continuedl: True` is set, so interrupted transfers do resume.
- ✅ ANSI stripping of `m`-terminated codes works correctly for colour.

---

## 7. Gaps / missing features

| Gap | Impact |
|---|---|
| **No configuration** — quality, resolution, naming template, subtitle language, audio format all hardcoded | Every change requires editing source |
| **No subtitle support** at all | Given your `cinesubz` links are explicitly *Sinhala-sub* sites, this is a notable omission |
| **No thumbnail / metadata embedding** | Files lose title/artist in the media player |
| **No playlist support** — `noplaylist: True` means a playlist URL yields only its first video | Silently drops the rest |
| **No de-duplication** of repeated URLs | Wastes bandwidth on repeats |
| **No log file** — all diagnostics live on a console that closes on exit | Failures are unrecoverable after the fact |
| **No dry-run / `--list-formats` mode** | You can't inspect what a link offers before committing |
| **No `Ctrl+C` handling** | Interrupting mid-queue dumps a traceback and leaks partial files |
| **No per-item error detail in the summary** | You get `3 failed` with no record of which ones or why |
| **No disk-space preflight check** | A 1080p batch can fill the drive mid-run |
| **No README / .gitignore / git** | No history, no sharing, no safety net |
| **No tests** | Every regression is discovered manually, mid-download |
| **No `ffmpeg_location` passed to yt-dlp** | yt-dlp may fail to find ffmpeg for DASH fixups even when you located it yourself |

---


## 8. Security & hygiene

> **This is the most urgent non-code item.**

`links.txt` currently contains **roughly 15 live API credentials** stored in plaintext — for
Bynara Router, AgentRouter, NVIDIA NIM (2×), Token Router, HCNSEC (2×), OpenCode (2×), InceptionLabs,
OrcaRouter, OpenRouter (3×), Z.AI (2×), Zyloo, Groq, and Alibaba DashScope — plus an LMS-style token
pair. It also contains personal study notes and a ranked model leaderboard.

Issues:

1. **Plaintext credentials on disk**, unencrypted, in a folder with no access control.
2. **They are inside the download queue.** `get_links()` submits every non-empty line to yt-dlp (B2),
   so key material is passed to the network as a "URL" — generating 15 pointless failed requests and
   writing them into error output.
3. **No `.gitignore` and no git repo** — the moment this folder is initialised or shared, all keys
   are committed.

**Recommended actions:**

- **Rotate all of these keys.** Treat them as compromised — they've been sitting in a file that
  gets parsed and printed to consoles.
- Move credentials to a `.env`, Windows Credential Manager, or a git-ignored `secrets.txt` outside
  the project folder.
- **Split the files:** `links.txt` should contain *only* URLs. Move notes/keys to a separate file
  outside the downloader directory.
- Add a `.gitignore` covering `links.txt`, `secrets*`, `__pycache__/`, `video/`, `audio/`,
  `downloads/`, `recycle/` before any `git init`.

*Legal note, offered neutrally:* several links in the list point at streaming/piracy aggregators.
That's a personal-use judgement outside the scope of this audit, but it's worth knowing those
aggregators rotate domains constantly (visible above: `.to` → `.site`, `.lk` → `.co`), which is
exactly why B16 shows them failing.

---


## 9. Prioritised fix plan

### P0 — makes it actually work (~1 hour)

| # | Fix | Where |
|---|---|---|
| 1 | Add fallback chain to the format selector | `ytd.py:88` |
| 2 | Filter to real URLs; honour `---end---`; dedupe | `ytd.py:159-184` |
| 3 | Pass `info` through to `download_audio` (kill duplicate extract) | `ytd.py:97-119` |
| 4 | Replace glob guessing with `requested_downloads`, exclude `.part`/`.ytdl` | `ytd.py:51-55` |
| 5 | Add `cd /d "%~dp0"` to the launcher | `runner.bat:95` |
| 6 | Capture and surface the ffmpeg stderr tail | `ytd.py:146-150` |
| 7 | Wrap all `input()` calls in an EOF-safe `pause()` | `ytd.py:199,205,228` |

### P1 — makes it reliable (~half a day)

| # | Fix | Where |
|---|---|---|
| 8 | Proper Windows filename sanitiser (reserved names, trailing dot/space, control chars, 120-char cap) | `ytd.py:140` |
| 9 | `finally:` cleanup of partials on failure | `ytd.py:217-224` |
| 10 | Delete intermediates instead of moving to `recycle/` | `ytd.py:126-136` |
| 11 | `cookiesfrombrowser` support + stop swallowing warnings | `ytd.py:58-62, 93` |
| 12 | `ffmpeg_location` + `retries`/`fragment_retries` + `sleep_interval` | `ytd.py:87-96, 108-117` |
| 13 | Check `errorlevel` after every install; don't print `[OK]` on failure | `runner.bat:27,37,53,67,86` |
| 14 | Write a run log to `downy.log` | new |
| 15 | Rotate the API keys, split `links.txt` | — |

### P2 — makes it pleasant (~1 day)

| # | Feature |
|---|---|
| 16 | `config.json` — quality, resolution, naming template, subtitle langs, output dir |
| 17 | Subtitle support (`--write-subs`, `--sub-langs`) — directly relevant to your Sinhala links |
| 18 | `download_archive` for skip-completed + true resume |
| 19 | Embed thumbnail + metadata (`--embed-thumbnail --add-metadata`) |
| 20 | Playlist support (drop `noplaylist`, or add `--yes-playlist`) |
| 21 | Move `os.makedirs` out of module scope into `main()` |
| 22 | KB/TB tiers in `format_size`; generic ANSI regex; `Ctrl+C` handling |
| 23 | `.gitignore` + `git init` + README + `requirements.txt` |
| 24 | A venv so yt-dlp never touches system Python |

---


## 10. Appendix — how the findings were verified

- `ytd.py` parsed with `ast.parse` (232 lines, no syntax errors).
- **Format selector:** ran yt-dlp's own `build_format_selector()` against live `extract_info()` for all
  5 YouTube links in `links.txt`, comparing the current selector against a robust fallback chain.
  Results in the B1 table are real, not predicted.
- **Non-YouTube links:** attempted real extraction on 4 non-YouTube links; all returned
  `Unsupported URL` / `not a valid URL`.
- **`links.txt` parsing:** counted non-empty lines, `---end---` occurrences, and `http`-prefixed lines
  using the exact logic from `get_links()`.
- **`find_file()`:** created a synthetic directory containing `.mp4`, `.mp4.part`, `.mp4.ytdl`,
  `.webm` and `.f137.mp4`, and observed `glob(...)[0]` return `abc123_video.f137.mp4`.
- **Sanitiser / `format_size`:** called the real functions with adversarial inputs (reserved names,
  control chars, 200-char titles, byte counts from 0 to 2 TiB).
- **`input()` EOFError:** executed `input()` against a non-TTY stdin.
- **winget `-h`:** confirmed against Microsoft Learn's `winget install` documentation before
  reporting — it is valid (`--silent`), so it is **not** listed as a bug.
- All test scripts were written to `%TEMP%` and deleted afterwards. The project folder was not
  modified and no downloads were performed.

