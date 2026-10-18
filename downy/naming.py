"""Filesystem-safe names and collision-free output paths.

Two separate concerns that used to be tangled into ``merge()``:

1. ``safe_title`` -- turn an arbitrary video title into a name Windows will
   actually accept. The old sanitiser only stripped ``\\/:*?"<>|``, which
   left reserved device names, trailing dots/spaces, control characters,
   and unbounded length intact.

2. ``choose_output_path`` -- pick a path that never overwrites an existing
   file. The old code ran ``ffmpeg -y`` against ``f"{index}. {title}.mkv"``
   with no existence check, so re-running a queue silently replaced earlier
   downloads with different videos.
"""

import os
import re

#: Windows forbids these characters anywhere in a filename.
ILLEGAL_CHARS = '<>:"/\\|?*'

#: Reserved device names on Windows, with or without an extension.
RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

#: Conservative cap: leaves room for the index prefix, extension, and the
#: containing folder while staying far under MAX_PATH.
MAX_TITLE_LENGTH = 120

#: Windows strips trailing dots and spaces from names.
_TRAILING_JUNK = re.compile(r"[.\s]+$")

#: Control characters, plus the Unicode replacement/format characters that
#: some titles carry.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f\u200b-\u200f\u2028\u2029\ufeff]")

#: Collapse runs of whitespace introduced by stripping.
_MULTISPACE = re.compile(r"\s{2,}")


def safe_title(title, fallback="video", max_length=MAX_TITLE_LENGTH):
    """Return a filename component Windows accepts for ``title``.

    Idempotent: ``safe_title(safe_title(x)) == safe_title(x)``.
    """
    text = _CONTROL_CHARS.sub(" ", str(title or ""))
    text = "".join(ch for ch in text if ch not in ILLEGAL_CHARS)
    text = _MULTISPACE.sub(" ", text)
    text = text.strip()
    text = _TRAILING_JUNK.sub("", text)

    if not text or text in (".", ".."):
        return fallback

    stem, ext = os.path.splitext(text)
    if stem.strip().lower() in RESERVED_NAMES:
        text = f"_{text}" if not ext else f"_{stem}{ext}"

    if len(text) > max_length:
        # Truncate the stem only, so the extension survives.
        stem, ext = os.path.splitext(text)
        if len(ext) > 16:
            ext = ""
            stem = text
        keep = max(1, max_length - len(ext))
        text = stem[:keep].rstrip()
        text = _TRAILING_JUNK.sub("", text) or fallback

    return text


def unique_path(directory, name, ext):
    """Return ``directory/name.ext``, appending `` (2)``, `` (3)``... as needed.

    Never returns a path that already exists.
    """
    safe_name = safe_title(name)
    ext = ext.lstrip(".")
    candidate = os.path.join(directory, f"{safe_name}.{ext}")
    if not os.path.exists(candidate):
        return candidate

    counter = 2
    while True:
        candidate = os.path.join(directory, f"{safe_name} ({counter}).{ext}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1
        if counter > 9999:
            raise OSError(f"cannot find a free filename for {safe_name!r}")


def build_output_path(directory, index, title, ext, numbered=True):
    """Assemble the final output path for one item.

    ``numbered`` keeps the ``N. Title`` ordering the batch workflow relies
    on. When an item fails, later items keep their original index so a
    series never silently renumbers itself.
    """
    safe_name = safe_title(title)
    name = f"{index:02d}. {safe_name}" if numbered else safe_name
    return unique_path(directory, name, ext)
