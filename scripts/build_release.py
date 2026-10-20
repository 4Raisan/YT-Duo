"""Build compact local releases: Unix launchers and optional Windows exe."""
import argparse
import base64
import hashlib
import io
import os
import shutil
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "Releases"


def publish_artifact(source, destination):
    """Rename a completed same-volume artifact; retain its build descriptor.

    Compiling/copying straight into an inherited Low-integrity output folder
    can make Windows launch the EXE at Low integrity. Do not edit ACLs or
    integrity labels; preserve the normally built file by an atomic rename.
    """
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.stat().st_dev != destination.parent.stat().st_dev:
        raise RuntimeError("Build and release folders must be on the same volume for atomic publication.")
    source.replace(destination)

