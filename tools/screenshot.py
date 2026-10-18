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

TARGET_WIDTH = 680
TARGET_HEIGHT = 210


def build_app(expanded=False, running=False):
    """Create the window and fill it with representative content."""
    from downy.gui import DownyApp

    root = tk.Tk()
    app = DownyApp(root)
    root.geometry(f"{TARGET_WIDTH}x{TARGET_HEIGHT}+0+0")

    app.quality_combo.current(3)          # 1080p
    app._mode_label_var.set("Best quality")
    app._on_mode_selected()

    if expanded:
        app.toggle_settings(True)
        for url in [
            "https://www.youtube.com/watch?v=X98-gKKR4Bk",
            "https://www.youtube.com/watch?v=8qZELvy3-LY",
        ]:
            app.queue_list.insert("end", url)
        app._refresh_queue_ui()

    if running:
        app.url_var.set("https://www.youtube.com/watch?v=GczjVOpaPPI")
        # Go through the real event handlers so the window is in exactly the
        # state a live download would put it in.
        app._set_running(True)
        app._on_item_start({"index": 2, "total": 3})
        app._on_probe({"index": 2, "title": "The Best AWS Certifications 2026",
                       "video": "248 vp9 1920x1080 @ 30fps", "audio": "251 opus 130k"})
        app._on_progress({"index": 2, "label": "Video",
                          "percent": " 46.2%", "speed": "2.4 MB/s", "eta": "00:38"})
        app.append_log("Starting 3 item(s), mode=best, quality=1080")
        app.append_log("[1] I Ranked The Best And Worst Cloud Careers In 2026")
        app.append_log("[2] The Best AWS Certifications To Get You Hired In 2026")

    # Autosize resizes the window; force a full repaint so the captured
    # pixels match the final layout rather than the intermediate one.
    root.update_idletasks()
    for _ in range(6):
        root.update()
        time.sleep(0.12)
    root.update_idletasks()
    return root, app


def grab(root, path):
    """Capture just the window's client area.

    ``winfo_rootx/rooty`` report the client origin in logical pixels; the
    screen capture is physical, so each corner is scaled and clamped to the
    screen -- otherwise the crop runs past the window edge and picks up
    desktop pixels, which look like stray widgets in the image.
    """
    from PIL import ImageGrab

    root.lift()
    root.attributes("-topmost", True)
    root.deiconify()
    # Late-packing widgets (Cancel, the log) repaint a frame after they are
    # placed; settle the geometry and force a full expose before grabbing.
    for _ in range(10):
        root.update_idletasks()
        root.update()
        time.sleep(0.05)

    full = ImageGrab.grab()
    scale_x = full.width / root.winfo_screenwidth()
    scale_y = full.height / root.winfo_screenheight()

    left = int(root.winfo_rootx() * scale_x)
    top = int(root.winfo_rooty() * scale_y)
    width = int(root.winfo_width() * scale_x)
    height = int(root.winfo_height() * scale_y)

    right = min(left + width, full.width)
    bottom = min(top + height, full.height)

    img = full.crop((left, top, right, bottom))
    img.save(path)
    return img.size


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "gui-screenshot.png")
    expanded = "--expanded" in sys.argv
    running = "--running" in sys.argv

    root, app = build_app(expanded=expanded, running=running)
    try:
        size = grab(root, out)
        print(f"wrote {out} ({size[0]}x{size[1]})")
    finally:
        app.destroy()
        root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())





