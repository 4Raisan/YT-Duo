# Using YT-Duo

Paste a link: the app scans available quality in the background. Explicit video tiers glow when offered; **Best** stays automatic. The leading indicator inside the link field is gray while scanning, red for invalid input, and green when verified. The paste hint stays inside the empty field.

- **Video:** Best quality, Smaller file, or Most compatible; choose an automatic or capped resolution.
- **Music:** original audio without re-encoding, or MP3 with Best variable bitrate, 320k, 256k, 192k, or 128k. Conversion cannot improve the source.
- **Auto download:** check the box beside Video/Music to start each verified paste with the selected settings. It starts off each launch; links pasted during a download join the running batch.
- **Options / Terminal:** clear headings and active disclosure buttons. Opening either section closes the other, with no main-window scrollbar. Terminal starts expanded and reopens for every download; logs continue while hidden.
- **Pause / Resume / Stop:** compact Ⅱ / ▶ / ■ controls at the right end of the progress bar. Pause and Stop take effect at engine checkpoints; an active metadata or FFmpeg step may finish first.
- **Already downloaded:** with archive skipping enabled, the prompt appears only when the same selected streams, container and audio quality already exist in the chosen destination. A different resolution, container, Video/Music choice or MP3 bitrate is a separate download. Choose Again or Skip. The compact prompt closes after ten seconds and defaults to Skip; downloading again keeps the existing file.
- Queue several links, add links during a run, or load your own text file. Individual failures do not stop the queue. Single downloads use the title; queued items keep their numbered positions, including after a failure.
- Save to, Browse, and Open appear on the main screen. Options include live text sizes (90–125%), Always on top, and optional automatic folder opening after downloads. Eight themes (Dark, Light, Midnight, Green, Orange, Pink, Yellow, High contrast) and background update checks are available.

Portable Windows defaults to `YT-Duo Downloads` beside the executable. Setup defaults to `YT-Duo Downloads` inside your Windows Downloads folder. Videos go directly into that folder; music goes into its `Audio` subfolder. Browse changes the base folder at any time, and custom choices are saved. Legacy automatic destinations migrate to the new default once; existing media is left in its original location. If the selected destination cannot be created, the app asks for another folder. Portable settings, completion history and temporary streams live inside the private `.ytduo` folder beside the executable (hidden on Windows and dot-hidden on Linux/macOS). Existing recognized settings and work files migrate there without replacing preferences or finished media. Old URL-only history is retained privately but does not block downloads because it cannot identify a precise quality. Setup installs application files under `%LOCALAPPDATA%/Programs/YT-Duo` and keeps settings under `%LOCALAPPDATA%/YT-Duo/data`. Unix launchers use `~/YT-Duo`. Override the data location with `YTDUO_HOME`. Uninstalling preserves user data. The bundled runtime is cached outside the EXE folder under LocalAppData, falling back to the system temporary folder if necessary. It is required by the portable app; it is not a downloaded-media folder. Known inactive runtime versions are pruned, while active and legacy caches are preserved.

Successful verification deletes the downloaded source streams. The private `.ytduo/work/recycle` folder is cleared on launch and before a batch; completed media is preserved. Interrupted streams may remain in the temporary video/audio folders. Terminal keeps a fixed height, the cursor stays normal, and a rotating indicator inside the link field shows scanning. An empty Download click does nothing.


## Command line

```sh
python -m ytduo --audio --audio-format original VIDEO_URL
python -m ytduo --audio --audio-format mp3 --audio-bitrate 192 VIDEO_URL
python -m ytduo --help
```

## Split downloads

Enable **Split** beside Video / Music, then enter **From** and **To** as `HH:MM:SS`, `MM:SS`, or seconds. Choose quality and format as usual. Split starts off each time the app opens. A queue applies the same range and selected settings to each item; each video's duration is checked separately.

```sh
python -m ytduo --split-start 01:37:59 --split-end 01:56:34 VIDEO_URL
python -m ytduo --audio --split-start 60 --split-end 120 VIDEO_URL
```

yt-dlp resolves the selected streams. YT-Duo reads their MP4 SIDX / WebM cues, then requests only overlapping media ranges, with roughly 10 seconds of decoding pre-roll and 2 seconds after the end (rounded to fragment boundaries). FFmpeg trims those local fragments. Separate audio/video transfer is retained; video results are muxed into **MP4** for edit-list timing, keeping the selected codecs. Original music copies its audio codec; MP3 uses your selected encoding settings. Frame/packet boundaries can differ by a small fraction of a second.

Finished, indexed videos and archived livestreams are supported. Active live/DVR, HLS-only selections, missing file sizes, nested indexes and unsupported codecs fail with an explanation. The app never falls back to fetching the whole recording for a split. Expired URLs fail safely; retry starts with fresh metadata. There is no quality downgrade to make a split succeed.

Original AAC and Opus split audio uses an M4A/MP4 container, retaining its source codec; a player must support that codec. The video MP4 edit list hides decoder dependency frames at the boundaries without re-encoding them.

Output names include the time interval. Duplicate checks distinguish different ranges, qualities, formats and Video / Music. Split temporary files are private and cleaned after success, failure or stop. `received_media_and_index_bytes` in the engine report counts successful response-body reads (including retries), not ISP-billed traffic or metadata extraction.

Available in v4.7.0 portable, Setup, Unix launchers and source installs.

## Limits

Resolution selection does not upscale. A cap can be exceeded if no candidate satisfies it. Cancellation is cooperative; cross-run partial-download resume is not guaranteed. `--numbered` forces queue prefixes; `--no-numbered` disables them, including for batches. Updates check GitHub automatically at startup. A green neon **Update to latest** button appears only when a newer stable release is detected; it downloads that release, verifies SHA-256 and restarts the app. Finish active downloads first. Failed checks report an error and check again at next launch; failed transfers keep the update button for retry. Settings and media stay in place. Source installs require a source upgrade; builds before 4.6.4 require one manual upgrade. **Star on GitHub** opens the repository.

YT-Duo is independent of YouTube/Google. Download only content you have permission to save.
