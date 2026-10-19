"""Shared test bootstrap.

The package lives in ``src/`` so the repository root stays tidy. Adding it to
``sys.path`` here means every test module can just ``import ytduo`` without
repeating the path juggling, and ``python -m unittest discover -s tests``
works from the repository root.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")

for _path in (SRC, ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

#: Repository root, handy for tests that need fixtures.
REPO_ROOT = ROOT
