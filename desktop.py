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
import os
import socket
import sys
import threading
import webbrowser

from paths import DATA_DIR

# No console in the packaged app, so log to a file in the data folder.
logging.basicConfig(
    filename=os.path.join(DATA_DIR, "audible-watcher.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("desktop")


def _already_running(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


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
    import app as webapp  # imported here so logging is set up first

    url = f"http://{'localhost' if webapp.HOST in ('127.0.0.1', '0.0.0.0') else webapp.HOST}:{webapp.PORT}"
    background = "--background" in sys.argv

    if _already_running("127.0.0.1", webapp.PORT):
        log.info("already running; opening %s", url)
        webbrowser.open(url)
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
                     pystray.Menu.SEPARATOR,
                     pystray.MenuItem("Quit", on_quit))).run()


if __name__ == "__main__":
    main()
