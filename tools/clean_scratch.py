"""Remove the ACL-locked scratch directories left by early test runs.

Those folders were created with tempfile.mkdtemp() before the test helper
switched to a plain mkdir; mkdtemp stamps an owner-only ACL that the
current token cannot traverse, so they resist normal deletion. This tries
in escalating order: plain remove, then a permissions reset via icacls,
then rmdir.
"""

import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PATTERNS = ("ytduo-test-*", "tmp-*", "tmpwj*", "_guitest-*", "_test-*",
            "_live-*", "_lv-*", "_arch_test", "_scratch*")


def candidates():
    found = []
    for pat in PATTERNS:
        for path in glob.glob(os.path.join(HERE, pat)):
            if os.path.isdir(path):
                found.append(path)
    return sorted(set(found))


def has_content(path):
    try:
        return bool(os.listdir(path))
    except OSError:
        return False


def try_remove(path):
    """Attempt deletion; return True on success."""
    if has_content(path):
        print(f"  SKIP (not empty): {os.path.basename(path)}")
        return None

    try:
        os.rmdir(path)
        return True
    except OSError:
        pass

    # Reset the DACL so the current user can traverse and delete.
    try:
        subprocess.run(["icacls", path, "/reset", "/t", "/c", "/q"],
                       capture_output=True, timeout=30)
        subprocess.run(["icacls", path, "/grant", f"{os.environ.get('USERNAME')}:(OI)(CI)F",
                        "/t", "/c", "/q"],
                       capture_output=True, timeout=30)
        os.rmdir(path)
        return True
    except (OSError, subprocess.SubprocessError):
        pass

    try:
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", path],
                       capture_output=True, timeout=30)
        return not os.path.exists(path)
    except (OSError, subprocess.SubprocessError):
        return False


def main():
    paths = candidates()
    if not paths:
        print("nothing to clean")
        return 0

    print(f"found {len(paths)} scratch director(ies)")
    removed, skipped, failed = 0, 0, []
    for path in paths:
        result = try_remove(path)
        if result is True:
            removed += 1
        elif result is None:
            skipped += 1
        else:
            failed.append(os.path.basename(path))

    print(f"\nremoved: {removed}   skipped(non-empty): {skipped}   failed: {len(failed)}")
    if failed:
        print("still locked by ACL:")
        for name in failed:
            print("   ", name)
        print("\nThese are empty. If they still bother you, delete them from")
        print("Explorer as the owning user, or run this script outside a sandbox.")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
