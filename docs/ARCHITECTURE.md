# Architecture

Project date: October 18, 2026.

YT-Duo retains the original `downy` package. `python -m downy` enters `cli.main`; `--gui` opens `gui.main`.

```text
CLI / Tkinter GUI
        |
Settings + URLs
        |
engine.run_batch → archive check → metadata probe
        |
selector.pick_streams → fetch video/audio
        |
FFmpeg stream copy → FFprobe verification → completion archive
```

| Module | Responsibility |
|---|---|
| `config.py` | Settings, project-relative paths, mode precedence, atomic preference writes |
| `selector.py` | Pure ranking functions over format metadata |
| `naming.py` | Safe names and collision suffixes |
| `engine.py` | Network operations, retries, merging, verification, storage helpers, batch summaries |
| `cli.py` | Arguments, queue parsing, terminal output, exit status |
| `gui.py` | Desktop interface, mutable run queue, preferences, event handlers |
| `gui_worker.py` | Worker thread, cancellation event, event queue |
| `widgets.py`, `theme.py` | Canvas controls, palettes, animation |

The engine emits `(name, payload)` events. The CLI renders directly; GUI workers enqueue events for the Tk thread to drain. Tk widgets stay on their owning thread. Items run sequentially; failures are recorded and the batch continues. Cancellation summaries distinguish completed, failed, skipped, interrupted, and unstarted items.

Successful outputs are archived after verification. Entries store URL and output path; moving or deleting an output permits re-downloading. Archive writes and output naming are not coordinated across processes.

Selection does not re-encode. The efficient policy prefers efficient codecs without guaranteeing a file size. Verification checks metadata and positive duration; it is not a full decode or completeness check.

Python/Tk handle orchestration and UI; yt-dlp accesses source sites; FFmpeg/FFprobe run as subprocesses. Deno supports source-specific JavaScript challenges. No application server or required API credential exists.

See the README for storage behavior and known limitations. Private historical audits and runtime data are excluded from this distribution.
