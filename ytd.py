"""Backward-compatible launcher.

Older instructions and ``runner.bat`` invoke ``python ytd.py``. That still
works: everything now lives in the ``downy`` package and this module is a
thin shim. New code should use ``python -m downy``.
"""

import sys

from downy.cli import main

if __name__ == "__main__":
    sys.exit(main())
