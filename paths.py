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
