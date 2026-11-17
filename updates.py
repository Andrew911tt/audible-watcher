"""Check GitHub for a newer Audible Watcher release.

One small request to GitHub's public releases API, only when you click
"Check for updates" or (if left on in Settings) once a day with the daily
check. Nothing about you or your watches is sent: just a generic
User-Agent with the version number. Nothing is downloaded or installed.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

import db
from paths import RESOURCE_DIR
from version import __version__

log = logging.getLogger(__name__)

REPO = "Andrew911tt/audible-watcher"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
CACHE_FOR = timedelta(hours=12)
TIMEOUT = 5  # seconds: never hold up a page or a check for long

# where the last answer is kept (the settings table; none of these are
# on the Settings form)
_KEYS = ("update_latest", "update_url", "update_download", "update_etag",
         "update_checked_at", "update_error")


def parse_version(v: str | None) -> tuple | None:
    """'v1.0.10' -> (1, 0, 10); None if it isn't a plain version number."""
    v = (v or "").strip().lstrip("vV")
    parts = v.split(".")
    if not v or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def is_newer(latest: str | None, current: str = __version__) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


def install_type() -> str:
    """How this copy was installed, for the "how to update" steps."""
    if getattr(sys, "frozen", False):
        return "windows"
    if os.path.exists("/.dockerenv"):
        return "docker"
    if os.path.isdir(os.path.join(RESOURCE_DIR, ".git")):
        return "git"
    return "manual"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fetch(etag: str | None) -> requests.Response:
    """The one request to GitHub (tests replace this)."""
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": f"AudibleWatcher/{__version__}"}
    if etag:
        headers["If-None-Match"] = etag  # "not modified" is free
    return requests.get(LATEST_URL, headers=headers, timeout=TIMEOUT)


def _explain(r: requests.Response) -> str:
    if r.status_code in (403, 429):
        return "GitHub's limit for checks was reached; try again later"
    if r.status_code == 404:
        return "no published release was found"
    return f"GitHub answered HTTP {r.status_code}"


def status() -> dict:
    """What's known from the last check, without asking GitHub."""
    s = db.get_settings()
    latest = s.get("update_latest") or None
    return {
        "current": __version__,
        "latest": latest,
        "available": is_newer(latest),
        "url": s.get("update_url") or RELEASES_PAGE,
        "download": s.get("update_download") or None,
        "checked_at": s.get("update_checked_at") or None,
        "error": s.get("update_error") or None,
        "install": install_type(),
        "dismissed": s.get("update_dismissed") == latest and bool(latest),
    }


def check(force: bool = False) -> dict:
    """Ask GitHub for the latest release (at most every 12 hours unless
    force=True) and return status(). Never raises: a failure is reported
    in status()["error"] and the previous answer is kept."""
    s = db.get_settings()
    checked = s.get("update_checked_at")
    if not force and checked:
        try:
            when = datetime.fromisoformat(checked.replace("Z", "+00:00"))
            if _now() - when < CACHE_FOR:
                return status()
        except ValueError:
            pass
    stamp = _now().strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        r = _fetch(s.get("update_etag") or None)
        if r.status_code == 304 and s.get("update_latest"):
            db.save_settings({"update_checked_at": stamp, "update_error": ""})
            return status()
        if r.status_code != 200:
            raise ValueError(_explain(r))
        try:
            data = r.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise ValueError("GitHub's answer wasn't understood")
        tag = data.get("tag_name")
        if parse_version(tag) is None:
            raise ValueError("GitHub's answer wasn't understood")
        download = next(
            (a.get("browser_download_url") for a in data.get("assets") or []
             if isinstance(a, dict)
             and str(a.get("name", "")).lower().endswith(".exe")), None)
        page = str(data.get("html_url") or "")
        db.save_settings({
            "update_latest": tag.lstrip("vV"),
            "update_url": page if page.startswith("https://github.com/")
            else RELEASES_PAGE,
            "update_download": download if str(download or "").startswith(
                "https://github.com/") else "",
            "update_etag": r.headers.get("ETag", ""),
            "update_checked_at": stamp, "update_error": ""})
    except (requests.RequestException, ValueError) as e:
        reason = (str(e) if isinstance(e, ValueError)
                  else "couldn't reach GitHub")
        log.info("Update check failed: %s", reason)
        db.save_settings({"update_checked_at": stamp,
                          "update_error": reason})
    return status()


def dismiss(version: str):
    """Hide the dashboard banner for this version (a newer one shows it
    again)."""
    db.save_settings({"update_dismissed": version})
