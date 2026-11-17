"""Desktop launcher used by the Windows installer build.

Runs Audible Watcher in the background with a tray icon instead of a console
window. Double-clicking it again (or the Start-menu shortcut) just opens the
browser if it's already running.

    python desktop.py               start and open the browser
    python desktop.py --background  start without opening the browser
                                    (used by the "start with Windows" option)

Needs the extra packages in requirements-desktop.txt (waitress, pystray, Pillow).
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import json
import socket
import sys
import threading
import urllib.request
import webbrowser

from paths import DATA_DIR

log = logging.getLogger("desktop")


def _setup_logging():
    """No console in the packaged app, so log to a file in the data folder,
    rotated so it can't grow forever (about 3 MB at most)."""
    logging.basicConfig(
        handlers=[logging.handlers.RotatingFileHandler(
            os.path.join(DATA_DIR, "audible-watcher.log"),
            maxBytes=1_000_000, backupCount=2, encoding="utf-8")],
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _port_status(port: int) -> str:
    """'free', 'ours' (Audible Watcher already running there) or 'other'
    (some other program has the port)."""
    with socket.socket() as s:
        s.settimeout(0.5)
        if s.connect_ex(("127.0.0.1", port)) != 0:
            return "free"
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz",
                                    timeout=3) as r:
            if json.load(r).get("app") == "audible-watcher":
                return "ours"
    except Exception:  # noqa: BLE001 - anything else means "not us"
        pass
    return "other"


def _show_error(message: str):
    """A visible error for the windowless app (Windows message box)."""
    log.error(message)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "Audible Watcher",
                                         0x10)


def _icon_image():
    """A simple headphones-on-orange icon drawn at runtime (no image files)."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 62, 62), fill=(242, 140, 40, 255))
    d.arc((16, 14, 48, 46), start=180, end=360, fill="white", width=5)
    d.rounded_rectangle((13, 30, 22, 46), radius=3, fill="white")
    d.rounded_rectangle((42, 30, 51, 46), radius=3, fill="white")
    return img


def main() -> None:
    _setup_logging()
    import app as webapp  # imported after logging is set up

    url = f"http://{'localhost' if webapp.HOST in ('127.0.0.1', '0.0.0.0') else webapp.HOST}:{webapp.PORT}"
    background = "--background" in sys.argv

    status = _port_status(webapp.PORT)
    if status == "ours":
        log.info("already running; opening %s", url)
        webbrowser.open(url)
        return
    if status == "other":
        _show_error(f"Another program is already using port {webapp.PORT}, "
                    "so Audible Watcher can't start. Close that program, "
                    "or set the AW_PORT environment variable to a free "
                    "port.")
        return

    from waitress import serve
    webapp.start_scheduler()
    threading.Thread(target=serve, args=(webapp.app,),
                     kwargs={"host": webapp.HOST, "port": webapp.PORT,
                             "threads": 8},
                     daemon=True).start()
    log.info("serving on %s (data in %s)", url, DATA_DIR)
    if not background:
        webbrowser.open(url)

    import pystray

    def on_open(icon, item):
        webbrowser.open(url)

    def on_updates(icon, item):
        webbrowser.open(url + "/settings#updates")

    def show_updates(item):
        # read each time the menu opens, so the Settings checkbox applies
        # without a restart
        try:
            return webapp.db.get_settings().get("update_tray_item",
                                                "1") == "1"
        except Exception:  # noqa: BLE001 - a menu must never fail
            return True

    def on_data(icon, item):
        os.startfile(DATA_DIR) if sys.platform == "win32" else webbrowser.open(DATA_DIR)

    def on_quit(icon, item):
        log.info("quit from tray")
        webapp.scheduler.shutdown(wait=False)
        icon.stop()

    pystray.Icon("audible-watcher", _icon_image(), "Audible Watcher",
                 menu=pystray.Menu(
                     pystray.MenuItem("Open Audible Watcher", on_open, default=True),
                     pystray.MenuItem("Open data folder", on_data),
                     pystray.MenuItem("Check for updates", on_updates,
                                      visible=show_updates),
                     pystray.Menu.SEPARATOR,
                     pystray.MenuItem("Quit", on_quit))).run()


if __name__ == "__main__":
    main()
