"""Where Audible Watcher keeps its files.

RESOURCE_DIR holds the code's own files (templates, static). In a PyInstaller
build those are unpacked to a temporary folder (sys._MEIPASS).

DATA_DIR holds your data: watcher.db and audible_auth.json.
  - AW_DATA_DIR, if set, always wins.
  - Running from source: the folder app.py is in (the original behaviour).
  - Installed build: a per-user folder, because Program Files isn't writable:
      Windows  %LOCALAPPDATA%\\Audible Watcher
      macOS    ~/Library/Application Support/Audible Watcher
      Linux    $XDG_DATA_HOME/audible-watcher (default ~/.local/share/...)
"""
from __future__ import annotations

import os
import sys

FROZEN = getattr(sys, "frozen", False)

RESOURCE_DIR = getattr(sys, "_MEIPASS",
                       os.path.dirname(os.path.abspath(__file__)))


def _default_data_dir() -> str:
    if not FROZEN:
        return os.path.dirname(os.path.abspath(__file__))
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "Audible Watcher")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Audible Watcher")
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "audible-watcher")


DATA_DIR = os.environ.get("AW_DATA_DIR") or _default_data_dir()
os.makedirs(DATA_DIR, exist_ok=True)


def check_data_dir_writable():
    """Stop with a clear message (not a database traceback) if the data
    folder can't be written — e.g. a Docker volume created by 1.0.0/1.0.1,
    which ran as root, used with the non-root image from 1.0.2."""
    probe = os.path.join(DATA_DIR, ".write-test")
    try:
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as e:
        uid = os.getuid() if hasattr(os, "getuid") else None
        msg = (f"Audible Watcher can't write to its data folder {DATA_DIR} "
               f"({e.strerror}).")
        if uid is not None:
            msg += (f" It runs as user {uid}. On Docker, give that user the "
                    f"folder once: sudo chown -R {uid}:{uid} ./data")
        sys.stderr.write(msg + "\n")
        raise SystemExit(2)
