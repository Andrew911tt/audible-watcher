"""Original publish dates for books, so the GUI can show when the print or
ebook edition came out before the Audible release.

Sources: Google Books (full dates, but a small unauthenticated quota that
rate-limits quickly) with Open Library as fallback (year precision, generous
limits). A rate-limited or failed lookup raises RateLimited so the caller
defers the rest of the batch instead of recording a false "no match".
(Goodreads retired its public API in 2020, so it can't be used here.)
"""
from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

GOOGLE_URL = "https://www.googleapis.com/books/v1/volumes"
OPENLIB_URL = "https://openlibrary.org/search.json"
HEADERS = {"User-Agent": "AudibleWatcher/1.0 (personal new-release monitor)"}


class RateLimited(Exception):
    """Metadata sources temporarily unavailable — retry on a later check."""


def _norm(s: str | None) -> str:
    return "".join(ch for ch in (s or "").lower()
                   if ch.isalnum() or ch == " ").strip()


def _titles_match(a: str, b: str) -> bool:
    return bool(a) and bool(b) and (a in b or b in a)


def _google_published(title: str, author: str | None) -> tuple[str | None, bool]:
    """(earliest edition date, source_answered)"""
    q = f'intitle:"{title}"'
    if author:
        q += f' inauthor:"{author}"'
    try:
        r = requests.get(GOOGLE_URL, params={"q": q, "maxResults": 10,
                                             "printType": "books"},
                         headers=HEADERS, timeout=15)
    except requests.RequestException as e:
        log.warning("Google Books lookup failed for %r: %s", title, e)
        return None, False
    if r.status_code != 200:
        log.info("Google Books HTTP %s for %r", r.status_code, title)
        return None, False
    tnorm = _norm(title)
    dates = [vi.get("publishedDate")
             for it in (r.json().get("items") or [])
             if (vi := it.get("volumeInfo") or {})
             and _titles_match(_norm(vi.get("title")), tnorm)
             and vi.get("publishedDate")]
    return (min(dates) if dates else None), True


def _openlib_published(title: str, author: str | None) -> tuple[str | None, bool]:
    """(first publish year as 'YYYY', source_answered)"""
    params = {"title": title, "limit": 5,
              "fields": "title,first_publish_year"}
    if author:
        params["author"] = author
    try:
        r = requests.get(OPENLIB_URL, params=params, headers=HEADERS,
                         timeout=20)
    except requests.RequestException as e:
        log.warning("Open Library lookup failed for %r: %s", title, e)
        return None, False
    if r.status_code != 200:
        log.info("Open Library HTTP %s for %r", r.status_code, title)
        return None, False
    tnorm = _norm(title)
    for d in (r.json().get("docs") or []):
        if _titles_match(_norm(d.get("title")), tnorm) and \
                d.get("first_publish_year"):
            return str(d["first_publish_year"]), True
    return None, True


def published_date(title: str,
                   author: str | None = None) -> tuple[str | None, bool]:
    """Earliest known publish date (YYYY[-MM[-DD]]) of any edition.

    Returns (date, definitive). definitive=False means a source was down and
    the miss shouldn't be recorded — retry on a later check. Raises
    RateLimited when no source is reachable at all.
    """
    gb_date, gb_ok = _google_published(title, author)
    if gb_date:
        return gb_date, True
    ol_date, ol_ok = _openlib_published(title, author)
    if ol_date:
        return ol_date, True
    if gb_ok and ol_ok:
        return None, True
    if not gb_ok and not ol_ok:
        raise RateLimited(f"no metadata source reachable for {title!r}")
    return None, False
