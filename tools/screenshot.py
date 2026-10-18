"""Render the GUI to a PNG for the README.

    python tools/screenshot.py [output.png]

Corrects for display scaling: Tk reports logical pixels while ImageGrab
captures physical ones, so the raw window rectangle would crop the right
and bottom edges on a scaled display.
"""

import os
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TARGET_WIDTH = 920
TARGET_HEIGHT = 780


def build_populated_app():
    """Create the window and fill it with representative content."""
    from downy.gui import DownyApp

    root = tk.Tk()
    app = DownyApp(root)
    root.geometry(f"{TARGET_WIDTH}x{TARGET_HEIGHT}+0+0")

    app.url_var.set("https://www.youtube.com/watch?v=GczjVOpaPPI")
    for url in [
        "https://www.youtube.com/watch?v=X98-gKKR4Bk",
        "https://www.youtube.com/watch?v=8qZELvy3-LY",
        "https://www.youtube.com/watch?v=tG1gzsHM5m8",
    ]:
        app.queue_list.insert("end", url)

    app.quality_combo.current(3)          # 1080p
    app.mode_var.set("best")
    app._update_mode_help()

    app.status_var.set("[2/4] Downloading…")
    app.progress.configure(value=46)
    app.detail_var.set("Video  46.2%   2.4MiB/s   ETA 00:38")

    app.append_log("── Starting 4 item(s), mode=best, quality=1080")
    app.append_log("[1] I Ranked The Best And Worst Cloud Careers In 2026")
    app.append_log("      video: 137 avc1 1920x1080 @ 25fps 4200k")
    app.append_log("      audio: 251 opus 130k")
    app.append_log("      Video 412.0 MB done")
    app.append_log("      Audio 24.1 MB done")
    app.append_log("      OK 1920x1080 @ 25.0fps h264/opus (1204.0s)", "ok")
    app.append_log("      saved: 01. I Ranked The Best And Worst Cloud Careers.mkv")
    app.append_log("[2] The Best AWS Certifications To Get You Hired In 2026")
    app.append_log("      video: 248 vp9 1920x1080 @ 30fps 3800k")

    root.update_idletasks()
    root.update()
    return root, app


def grab(root, path):
    from PIL import ImageGrab

    root.lift()
    root.attributes("-topmost", True)
    root.update()
    time.sleep(0.8)
    root.update()

    full = ImageGrab.grab()
    scale_x = full.width / root.winfo_screenwidth()
    scale_y = full.height / root.winfo_screenheight()

    x = int(root.winfo_rootx() * scale_x)
    y = int(root.winfo_rooty() * scale_y)
    w = int(root.winfo_width() * scale_x)
    h = int(root.winfo_height() * scale_y)

    img = full.crop((x, y, min(x + w, full.width), min(y + h, full.height)))
    img.save(path)
    return img.size


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "gui-screenshot.png")

    root, app = build_populated_app()
    try:
        size = grab(root, out)
        print(f"wrote {out} ({size[0]}x{size[1]})")
    finally:
        app.destroy()
        root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
