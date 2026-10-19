"""Render the GUI to PNGs for the README.

    python tools/screenshot.py

Produces gui-idle.png, gui-running.png and gui-light.png. The window must be
mapped for a screen grab, so this needs a desktop session.
"""

import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from downy.gui import DownyApp  # noqa: E402
from tools.capture import capture  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(HERE, "docs")


def make_app(theme="dark"):
    root = tk.Tk()
    app = DownyApp(root)
    app.apply_theme(theme)
    # Do not pin the geometry: the app sizes itself to its visible panels,
    # and a fixed size would clip the Cancel row during a run.
    root.update()
    return root, app


def populate_running(app):
    app.url_var.set("https://www.youtube.com/watch?v=GczjVOpaPPI")
    app._on_quality(1080)
    app._set_running(True)
    app._set_progress_visible(True)
    app._on_item_start({"index": 2, "total": 3})
    app._on_probe({"index": 2, "title": "The Best AWS Certifications 2026",
                   "video": "vp9 1920x1080 @ 30fps 3800k",
                   "audio": "opus 130k"})
    app.append_log("Starting 3 item(s) - format best, quality 1080", "muted")
    app.append_log("[1] I Ranked The Best And Worst Cloud Careers In 2026", "info")
    app.append_log("    done 1920x1080 @ 25.0fps h264/opus", "ok")
    app._on_progress({"index": 2, "label": "Video", "percent": " 46.2%",
                      "speed": "2.4 MB/s", "eta": "00:38"})
    app.progress.set(46.2, immediate=True)


def shoot(theme, out_name, running=False):
    os.makedirs(DOCS, exist_ok=True)
    root, app = make_app(theme)
    if running:
        populate_running(app)
    # The app resizes itself as panels appear; give those geometry changes
    # time to land before measuring, then grab.
    for _ in range(12):
        root.update_idletasks()
        root.update()
    needed = root.winfo_reqheight()
    if root.winfo_height() < needed:
        root.geometry(f"{root.winfo_width()}x{needed}")
        root.update()
    size = capture(root, os.path.join(DOCS, out_name))
    print(f"wrote {out_name} {size}")
    app.destroy()
    root.destroy()


if __name__ == "__main__":
    shoot("dark", "gui-idle.png")
    shoot("dark", "gui-running.png", running=True)
    shoot("light", "gui-light.png")
