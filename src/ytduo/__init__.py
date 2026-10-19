"""YT-Duo -- download YouTube videos in the best available quality.

The package is split so every layer above the engine stays optional:

    ytduo.config      Settings: paths, tunables, mode resolution
    ytduo.selector    Pure format-ranking logic (no I/O, no network)
    ytduo.naming      Filesystem-safe names, collision-free paths
    ytduo.engine      The download pipeline. UI-agnostic; reports progress
                      through a callback and never prints or prompts.
    ytduo.cli         Terminal front-end (argparse + console output).
    ytduo.gui         Desktop front-end (tkinter + Canvas widgets).
    ytduo.theme       Dark/light palettes and easing helpers.
    ytduo.widgets     Custom-drawn buttons, chips and progress bar.
    ytduo.gui_worker  Thread/queue bridge keeping Tk single-threaded.

Because ``ytduo.engine`` takes a Settings object and an event callback, a
front-end needs no knowledge of yt-dlp, ffmpeg, or the folder layout -- it
calls ``run_batch(urls, settings, emit=...)`` and renders what comes back.
"""

__version__ = "4.5.1"
__app_name__ = "YT-Duo"
__tagline__ = "Download YouTube videos in the best available quality."

__all__ = ["__version__", "__app_name__", "__tagline__"]
