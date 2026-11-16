"""Write packaging/icon.ico for the build (same drawing as the tray icon)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AW_DATA_DIR", os.path.join(os.path.dirname(__file__), "build-data"))

from desktop import _icon_image  # noqa: E402

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
_icon_image().save(out, sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
print("wrote", out)
