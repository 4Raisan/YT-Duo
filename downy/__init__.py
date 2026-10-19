"""YT-Duo -- a batch video downloader built on yt-dlp.

The package is split so that every layer above the engine is optional:

    downy.config     Settings: paths, tunables, mode resolution
    downy.selector   Pure format-ranking logic (no I/O, no network)
    downy.naming     Filesystem-safe filenames
    downy.engine     The download pipeline. UI-agnostic; reports progress
                     through a callback and never prints or prompts.
    downy.cli        The terminal front-end (argparse + console output).
    downy.gui        Tkinter desktop interface
    downy.web        (future) paste-a-link in a browser

Because ``downy.engine`` takes a Settings object and a progress callback,
a GUI or web front-end needs no knowledge of yt-dlp, ffmpeg, or the
directory layout -- it calls ``run_batch(urls, settings, on_event)`` and
renders whatever events arrive.
"""

__version__ = "2.1.0"

__all__ = ["__version__"]
