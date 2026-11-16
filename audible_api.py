"""Thin client for Audible's public catalog API.

Uses the unauthenticated catalog endpoint:
    https://api.audible.{tld}/1.0/catalog/products
No login or Audible credentials are required.
"""
from __future__ import annotations

import logging
import re
import time
from urllib.parse import urlencode

import requests

log = logging.getLogger(__name__)

RESPONSE_GROUPS = "product_desc,product_attrs,contributors,series,media"
HEADERS = {
    "User-Agent": "AudibleWatcher/1.0 (personal new-release monitor)",
    "Accept": "application/json",
}

# language values as they appear in Audible catalog data
LANGUAGES = ["english", "german", "french", "spanish", "italian", "portuguese",
             "dutch", "polish", "swedish", "danish", "norwegian", "finnish",
             "russian", "japanese", "hindi"]

# marketplace -> API host TLD
MARKETPLACES = {
    "us": "com",
    "uk": "co.uk",
    "ca": "ca",
    "au": "com.au",
    "de": "de",
    "fr": "fr",
    "in": "in",
    "it": "it",
    "es": "es",
    "jp": "co.jp",
}


class AudibleAPIError(Exception):
    pass


class AudibleUnavailable(AudibleAPIError):
    """Audible couldn't be reached (network error, rate limit or server
    error after retries) — as opposed to a bad request for one book."""


def matches_language(book: dict, language: str | None) -> bool:
    """True if the book is in the given language. Books whose language the
    catalog doesn't state are kept (benefit of the doubt)."""
    if not language:
        return True
    lang = (book.get("language") or "").strip().lower()
    return not lang or lang == language.strip().lower()


def _base_url(marketplace: str) -> str:
    tld = MARKETPLACES.get(marketplace, "com")
    return f"https://api.audible.{tld}/1.0/catalog/products"


def _store_url(marketplace: str, asin: str) -> str:
    tld = MARKETPLACES.get(marketplace, "com")
    return f"https://www.audible.{tld}/pd/{asin}"


def series_page_url(marketplace: str, series_asin: str | None,
                    series_title: str = "") -> str:
    """The series' page in the Audible store (by ID, which Audible
    redirects to the full page), or a store search when the ID is unknown."""
    tld = MARKETPLACES.get(marketplace, "com")
    if series_asin:
        return f"https://www.audible.{tld}/series/{series_asin}"
    return (f"https://www.audible.{tld}/search?"
            + urlencode({"keywords": series_title}))


def store_prefix(marketplace: str) -> str:
    """Start of every book link in this marketplace's store."""
    return _store_url(marketplace, "")


RETRY_STATUS = {429, 500, 502, 503, 504}  # worth another try
MAX_RETRY_AFTER = 60  # seconds; never stall a check longer than this


def _retry_after(r) -> float | None:
    """The server's Retry-After (in seconds), capped, if it sent one."""
    try:
        return min(max(float(r.headers.get("Retry-After", "")), 0),
                   MAX_RETRY_AFTER)
    except (TypeError, ValueError):
        return None


def _get_json(url: str, params: dict, attempts: int = 3) -> dict:
    """GET and decode JSON. Network errors, rate limits (429) and server
    errors (5xx) are retried with backoff, honouring Retry-After; other
    HTTP errors (bad request, not found...) fail at once."""
    last_err, transient = None, True
    for attempt in range(attempts):
        wait = 2 * (attempt + 1)
        try:
            # 10 s to connect (a dead network fails fast), 30 s to answer
            r = requests.get(url, params=params, headers=HEADERS,
                             timeout=(10, 30))
        except requests.RequestException as e:
            last_err = str(e)
        else:
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    raise AudibleAPIError(
                        "Audible API returned a response that isn't JSON")
            last_err = f"HTTP {r.status_code}"
            if r.status_code not in RETRY_STATUS:
                transient = False
                break
            wait = _retry_after(r) or wait
        if attempt < attempts - 1:
            time.sleep(wait)
    error = AudibleUnavailable if transient else AudibleAPIError
    raise error(f"Audible API request failed: {last_err}")


def norm_name(name: str | None) -> str:
    """Compare-friendly author name: 'J.R.R. Tolkien' == 'J. R. R. Tolkien'."""
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def _credited_names(author_list: list[str]) -> set[str]:
    """Normalised author credits, splitting co-author credits the catalog
    sometimes lumps together ('Brandon Sanderson & Dan Wells'). Credits with
    a role ('Larry Correia - foreword', 'X (Introduction)') stay as they are,
    so a foreword or translation doesn't count as the author's own book."""
    names = set()
    for credit in author_list:
        names.add(norm_name(credit))  # the credit as written, '&' and all
        for part in re.split(r"\s+(?:&|and)\s+", credit):
            names.add(norm_name(part))
    return names


_NAME_SUFFIX = re.compile(
    r"^(jr|sr|ii|iii|iv|v|phd|md|esq|ret|obe|mbe|cbe)\.?$", re.IGNORECASE)


def split_authors(authors: str | None) -> list[str]:
    """Split a stored "A, B, C" author string back into names, keeping
    suffixes with their name: "Martin Luther King, Jr., Coretta Scott King"
    -> ["Martin Luther King, Jr.", "Coretta Scott King"]."""
    names: list[str] = []
    for part in (authors or "").split(","):
        part = part.strip()
        if not part:
            continue
        if names and _NAME_SUFFIX.match(part):
            names[-1] = f"{names[-1]}, {part}"
        else:
            names.append(part)
    return names


_ROLE_CREDIT = re.compile(r"\s[-–—]\s|\(")


def is_role_credit(name: str) -> bool:
    """A credit with a role attached, like "Larry Correia - foreword" or
    "Matt Dinniman (Introduction By)", rather than an author's own name."""
    return bool(_ROLE_CREDIT.search(name or ""))


def first_author(authors: str | None) -> str | None:
    names = split_authors(authors)
    return names[0] if names else None


def _get(marketplace: str, params: dict) -> dict:
    params = dict(params)
    params.setdefault("response_groups", RESPONSE_GROUPS)
    return _get_json(_base_url(marketplace), params)


def _simplify(product: dict, marketplace: str) -> dict:
    authors = [a.get("name", "") for a in (product.get("authors") or [])]
    narrators = [n.get("name", "") for n in (product.get("narrators") or [])]
    series_list = product.get("series") or []
    series_title = series_list[0].get("title") if series_list else None
    series_sequence = series_list[0].get("sequence") if series_list else None
    series_asin = series_list[0].get("asin") if series_list else None
    images = product.get("product_images") or {}
    image_url = images.get("500") or next(iter(images.values()), None)
    release_date = product.get("release_date")
    if release_date and release_date >= "2100":  # Audible's TBA placeholder
        release_date = None
    return {
        "asin": product.get("asin"),
        "title": product.get("title") or "",
        "subtitle": product.get("subtitle") or "",
        "authors": ", ".join(a for a in authors if a),
        "author_list": [a for a in authors if a],  # names may contain commas
        "narrators": ", ".join(n for n in narrators if n),
        "series_title": series_title,
        "series_sequence": series_sequence,
        "series_asin": series_asin,
        "all_series": [
            {"title": s.get("title"), "asin": s.get("asin"),
             "sequence": s.get("sequence")}
            for s in series_list
        ],
        "release_date": release_date,  # YYYY-MM-DD or None if TBA
        "runtime_minutes": product.get("runtime_length_min"),
        "language": product.get("language"),
        "image_url": image_url,
        "url": _store_url(marketplace, product.get("asin", "")),
    }


NEWEST_FIRST = "-ReleaseDate"
# An author watch fetches all of the author's titles. The cap only stops a
# runaway fuzzy match (Stephen King, the biggest so far, is ~400 titles in
# all languages: 8 requests).
WATCH_FETCH_LIMIT = 1000

# Bump this whenever a change makes watches fetch more or different titles
# (a bigger window, a different search). Each watch remembers the revision
# it last ran with; on its first check under a new one, titles that only
# turned up because of the change are recorded quietly instead of flagged
# as new releases. History: 1 = up to 1.0.2 (newest 100 per author);
# 2 = all of an author's titles.
FETCH_REVISION = 2


def search_by_author(name: str, marketplace: str = "us",
                     max_results: int = 100,
                     strict: bool = False,
                     sort: str | None = NEWEST_FIRST) -> list[dict]:
    """Titles credited to an author, newest first by default.

    sort=None uses Audible's relevance order: right for finding an author
    (the newest products of a translated author are mostly translations),
    wrong for watching one (new releases must be in the window).

    Audible's author parameter matches loosely. strict=True (used for
    watches) keeps only books credited to exactly this author, ignoring
    case, spaces and punctuation — never someone else's books. Otherwise
    (used for search) near matches are returned when there's no exact one.
    """
    out, page = [], 0
    while len(out) < max_results:
        num = min(50, max_results - len(out))
        params = {"author": name, "num_results": num, "page": page}
        if sort:
            params["products_sort_by"] = sort
        data = _get(marketplace, params)
        products = data.get("products") or []
        out.extend(_simplify(p, marketplace) for p in products)
        if len(products) < num:
            break
        page += 1
    if strict:
        target = norm_name(name)
        return [b for b in out if target in _credited_names(b["author_list"])]
    # The author param does fuzzy matching; keep only exact-ish matches.
    name_lc = name.strip().lower()
    exact = [b for b in out
             if name_lc in (b["authors"] or "").lower()]
    return exact if exact else out


def search_by_keywords(keywords: str, marketplace: str = "us",
                       max_results: int = 50) -> list[dict]:
    """Products matching keywords in Audible's relevance order. (Newest-first
    buried popular series under recent translations and look-alikes:
    "Dungeon Crawler Carl" and "Harry Potter" weren't found at all.)"""
    data = _get(marketplace, {
        "keywords": keywords,
        "num_results": min(50, max_results),
    })
    return [_simplify(p, marketplace) for p in (data.get("products") or [])]


def search_series_candidates(keywords: str, marketplace: str = "us",
                             language: str | None = None) -> list[dict]:
    """Search by keywords and return the distinct series found in results."""
    books = search_by_keywords(keywords, marketplace, max_results=50)
    seen: dict[str, dict] = {}
    for b in books:
        if not matches_language(b, language):
            continue
        for s in b["all_series"]:
            if not s.get("asin") or not s.get("title"):
                continue
            entry = seen.setdefault(s["asin"], {
                "series_asin": s["asin"],
                "series_title": s["title"],
                "count": 0,
                "sample_authors": b["authors"],
                "image_url": b["image_url"],
            })
            entry["count"] += 1
    # rank: exact title match, then titles containing the query, then by how
    # many search results belong to the series
    kw_lc = keywords.strip().lower()

    def rank(e):
        title = (e["series_title"] or "").strip().lower()
        pos = 0 if title == kw_lc else (1 if kw_lc in title else 2)
        return (pos, -e["count"])

    return sorted(seen.values(), key=rank)


def _series_child_asins(series_asin: str, marketplace: str = "us") -> list[str]:
    """Book ASINs belonging to a series, from the series product's relationships."""
    tld = MARKETPLACES.get(marketplace, "com")
    url = f"https://api.audible.{tld}/1.0/catalog/products/{series_asin}"
    data = _get_json(url, {"response_groups": "relationships"})
    rels = (data.get("product") or {}).get("relationships") or []
    return [rel["asin"] for rel in rels
            if rel.get("asin")
            and rel.get("relationship_to_product") == "child"
            and rel.get("relationship_type") == "series"]


def _fetch_by_asins(asins: list[str], marketplace: str = "us") -> list[dict]:
    out = []
    for i in range(0, len(asins), 50):
        chunk = asins[i:i + 50]
        data = _get(marketplace, {"asins": ",".join(chunk),
                                  "num_results": len(chunk)})
        out.extend(_simplify(p, marketplace)
                   for p in (data.get("products") or []))
    return out


def resolve_series_asin(series_title: str, marketplace: str = "us") -> str | None:
    """This store's ID for a series, found by its exact title."""
    return _resolve_series_asin(series_title, marketplace)


def _resolve_series_asin(series_title: str, marketplace: str = "us") -> str | None:
    """Find the series ASIN for an exact (case-insensitive) series title."""
    title_lc = (series_title or "").strip().lower()
    for cand in search_series_candidates(series_title, marketplace):
        if (cand["series_title"] or "").strip().lower() == title_lc:
            return cand["series_asin"]
    return None


def books_in_series(series_title: str, series_asin: str | None,
                    marketplace: str = "us", max_results: int = 100) -> list[dict]:
    """Books belonging to a series, matched by series ASIN (preferred) or title.

    The series product's relationships give the complete book list; a keyword
    search only surfaces books that rank in its top results, so it is kept
    only as a fallback.
    """
    if not series_asin:
        try:
            series_asin = _resolve_series_asin(series_title, marketplace)
        except AudibleUnavailable:
            raise  # Audible is down: a fallback search would fail too
        except AudibleAPIError:
            series_asin = None
    if series_asin:
        try:
            child_asins = _series_child_asins(series_asin, marketplace)
            if child_asins:
                # all of them: relationships can come in series order, so a
                # cap could drop exactly the newest books of a long series
                books = _fetch_by_asins(child_asins, marketplace)
                # newest first; TBA pre-orders (no date yet) on top
                books.sort(key=lambda b: b["release_date"] or "9999",
                           reverse=True)
                return books
        except AudibleUnavailable:
            raise
        except AudibleAPIError:
            log.warning("Series relationship lookup failed for %s; "
                        "falling back to keyword search", series_asin)

    books = search_by_keywords(series_title, marketplace, max_results=50)
    matched = []
    title_lc = (series_title or "").strip().lower()
    for b in books:
        for s in b["all_series"]:
            if series_asin and s.get("asin") == series_asin:
                matched.append(b)
                break
            if not series_asin and (s.get("title") or "").strip().lower() == title_lc:
                matched.append(b)
                break
    # Dedupe by ASIN
    seen, out = set(), []
    for b in matched:
        if b["asin"] and b["asin"] not in seen:
            seen.add(b["asin"])
            out.append(b)
    return out[:max_results]


def check_market_availability(asin: str, title: str,
                              author: str | None = None,
                              marketplace: str = "us") -> bool | None:
    """Whether the marketplace's own (region-filtered) store listing contains
    this ASIN. Series relationships return every regional edition, so books
    fetched that way can be listings that aren't sold locally at all.

    Returns None when the search comes back empty — no conclusion.
    """
    params = {"title": title, "num_results": 50}
    if author:
        params["author"] = author
    products = _get(marketplace, params).get("products") or []
    if not products and author:  # author name mismatch? retry title-only
        products = _get(marketplace,
                        {"title": title, "num_results": 50}).get("products") or []
    if not products:
        return None
    return any(p.get("asin") == asin for p in products)


def fetch_watch_books(watch_type: str, name: str, asin: str | None,
                      marketplace: str = "us") -> list[dict]:
    """Current Audible catalog entries for a watched author or series."""
    if watch_type == "author":
        # newest first, and all of them: heavily translated authors still
        # show their whole own-language back catalogue
        return search_by_author(name, marketplace, strict=True,
                                max_results=WATCH_FETCH_LIMIT)
    return books_in_series(name, asin, marketplace)
