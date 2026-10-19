"""Backward-compatible launcher.

Older instructions and ``runner.bat`` invoke ``python ytd.py``. That still
works: everything now lives in the ``ytduo`` package and this module is a
thin shim. New code should use ``python -m ytduo``.
"""

import sys

from ytduo.cli import main

if __name__ == "__main__":
    sys.exit(main())
