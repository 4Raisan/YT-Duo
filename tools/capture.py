"""Shared window-capture helper for the screenshot tools.

Screen grabs on Windows are physical pixels; Tk reports logical pixels on
the DPI-scaled display used here (1536x864 logical == 1920x1080 physical,
i.e. 1.25x). ``winfo_rootx/rooty`` give the client origin in logical
pixels, so scaling them by the ratio is the reliable way to land on the
window -- a constant offset cannot work because it depends on the window
frame and where the window manager placed it.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def settle(root, rounds=10, delay=0.04):
    """Let Tk finish layout, repaint late-packed widgets and run animations."""
    for _ in range(rounds):
        try:
            root.update_idletasks()
            root.update()
        except Exception:  # noqa: BLE001 - window may be closing
            return
        time.sleep(delay)


def capture(root, path):
    """Save the window's client area as a PNG and return its pixel size.

    The window is lifted and settled first: ``winfo_rootx/rooty`` and
    ``winfo_width/height`` only become final once the window manager has
    placed and resized the window, and grabbing too early crops whatever
    happened to be underneath (or an older, smaller size).
    """
    from PIL import ImageGrab

    root.lift()
    root.attributes("-topmost", True)
    settle(root, rounds=16, delay=0.05)

    scale_x = ImageGrab.grab().width / root.winfo_screenwidth()
    scale_y = ImageGrab.grab().height / root.winfo_screenheight()

    left = max(0, int(round(root.winfo_rootx() * scale_x)))
    top = max(0, int(round(root.winfo_rooty() * scale_y)))
    width = int(round(root.winfo_width() * scale_x))
    height = int(round(root.winfo_height() * scale_y))

    full = ImageGrab.grab()
    right = min(left + width, full.width)
    bottom = min(top + height, full.height)

    img = full.crop((left, top, right, bottom))
    img.save(path)
    return img.size
