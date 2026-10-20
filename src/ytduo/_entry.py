"""Entry point for the packaged application.

PyInstaller runs this module directly. It launches the desktop window and,
if start-up fails, writes the traceback next to the executable *and* shows a
dialog -- a windowed build has no console, so an unhandled exception would
otherwise vanish without a trace.
"""

import os
import sys
import traceback


def _report(exc_text):
    """Make a start-up failure visible: a log file and a dialog."""
    try:
        base = os.path.dirname(os.path.abspath(sys.executable))
    except Exception:  # noqa: BLE001
        base = os.getcwd()

    log_path = os.path.join(base, "ytduo-error.log")
    try:
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(exc_text)
    except OSError:
        log_path = None

    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "YT-Duo",
            "YT-Duo could not start.\n\n"
            + (f"Details were written to:\n{log_path}\n\n" if log_path else "")
            + exc_text.strip().splitlines()[-1][:300],
        )
        root.destroy()
    except Exception:  # noqa: BLE001 - no GUI to report through
        pass


def main():
    try:
        diagnostic = os.environ.get("YTDUO_SELF_TEST_REPORT")
        if diagnostic:
            import json
            from pathlib import Path
            from ytduo import __version__
            from ytduo.dependencies import report
            rows = report()
            Path(diagnostic).write_text(json.dumps({"version": __version__, "dependencies": rows},
                                                   indent=2), encoding="utf-8")
            return 0 if all(ok for _name, ok, _detail in rows) else 1
        from ytduo.gui import main as gui_main
        return gui_main()
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - top-level guard
        _report(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
