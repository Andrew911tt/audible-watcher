"""SQLite storage for Audible Watcher.

Data model: `books` holds one row per catalog entry (shared metadata such as
title, release date, language, publish date); `watch_books` links a book to
each watch that surfaced it and carries the per-watch state (new/seen,
notified, hidden). `owned_books` is a separate snapshot of the connected
Audible account's library.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone

from paths import DATA_DIR

DB_PATH = os.environ.get("AUDIBLE_WATCHER_DB",
                         os.path.join(DATA_DIR, "watcher.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS watches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    type            TEXT NOT NULL CHECK (type IN ('author', 'series')),
    name            TEXT NOT NULL,
    asin            TEXT,
    created_at      TEXT NOT NULL,
    last_checked_at TEXT,
    last_error      TEXT,
    UNIQUE (type, name)
);

CREATE TABLE IF NOT EXISTS books (
    asin            TEXT PRIMARY KEY,
    title           TEXT,
    subtitle        TEXT,
    authors         TEXT,
    narrators       TEXT,
    series_title    TEXT,
    series_sequence TEXT,
    release_date    TEXT,
    runtime_minutes INTEGER,
    language        TEXT,
    published_date  TEXT,
    available       INTEGER,  -- sold in our marketplace? NULL = unchecked
    image_url       TEXT,
    url             TEXT
);

CREATE TABLE IF NOT EXISTS watch_books (
    watch_id      INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
    asin          TEXT NOT NULL REFERENCES books(asin),
    discovered_at TEXT NOT NULL,
    is_new        INTEGER NOT NULL DEFAULT 0,
    notified      INTEGER NOT NULL DEFAULT 0,
    hidden        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (asin, watch_id)
);

CREATE TABLE IF NOT EXISTS owned_books (
    asin            TEXT PRIMARY KEY,
    title           TEXT,
    authors         TEXT,
    narrators       TEXT,
    series_title    TEXT,
    series_sequence TEXT,
    series_asin     TEXT,
    release_date    TEXT,
    runtime_minutes INTEGER,
    language        TEXT,
    image_url       TEXT,
    url             TEXT,
    purchase_date   TEXT,
    preordered      INTEGER NOT NULL DEFAULT 0
);

-- internal secrets, kept apart from user-facing settings
CREATE TABLE IF NOT EXISTS app_secrets (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS check_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at      TEXT NOT NULL,
    trigger     TEXT,
    new_books   INTEGER DEFAULT 0,
    errors      TEXT,
    email_sent  INTEGER DEFAULT 0
);
"""

DEFAULT_SETTINGS = {
    "marketplace": "us",
    "check_hour": "8",           # local hour of daily check
    "smtp_host": "smtp.gmail.com",
    "smtp_port": "465",
    "smtp_security": "ssl",      # ssl | starttls
    "smtp_user": "",
    "smtp_password": "",
    "email_from": "",
    "email_to": "",
    "email_enabled": "0",
    "language": "",              # '' = any language
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def _rename_legacy_books(c) -> bool:
    """v1 stored one wide row per (book, watch). Detect that layout and move
    it aside so SCHEMA can create the split tables; init_db copies the data
    over afterwards."""
    cols = {r["name"] for r in c.execute("PRAGMA table_info(books)")}
    if "watch_id" not in cols:
        return False
    # very old DBs may predate these columns; add them so the copy works
    for col, decl in [("hidden", "INTEGER NOT NULL DEFAULT 0"),
                      ("language", "TEXT"), ("published_date", "TEXT")]:
        if col not in cols:
            c.execute(f"ALTER TABLE books ADD COLUMN {col} {decl}")
    c.execute("ALTER TABLE books RENAME TO books_v1")
    return True


def init_db():
    with conn() as c:
        legacy = _rename_legacy_books(c)
        c.executescript(SCHEMA)
        if legacy:
            c.execute("""
                INSERT OR IGNORE INTO books
                (asin, title, subtitle, authors, narrators, series_title,
                 series_sequence, release_date, runtime_minutes, language,
                 published_date, image_url, url)
                SELECT asin, title, subtitle, authors, narrators,
                       series_title, series_sequence, release_date,
                       runtime_minutes, language, published_date, image_url,
                       url
                FROM books_v1""")
            c.execute("""
                INSERT OR IGNORE INTO watch_books
                (watch_id, asin, discovered_at, is_new, notified, hidden)
                SELECT watch_id, asin, discovered_at, is_new, notified,
                       hidden
                FROM books_v1""")
            c.execute("DROP TABLE books_v1")
        book_cols = {r["name"] for r in c.execute("PRAGMA table_info(books)")}
        if "available" not in book_cols:
            c.execute("ALTER TABLE books ADD COLUMN available INTEGER")
        owned_cols = {r["name"]
                      for r in c.execute("PRAGMA table_info(owned_books)")}
        for col, decl in [("authors", "TEXT"), ("narrators", "TEXT"),
                          ("series_title", "TEXT"),
                          ("series_sequence", "TEXT"),
                          ("series_asin", "TEXT"),
                          ("release_date", "TEXT"),
                          ("runtime_minutes", "INTEGER"),
                          ("language", "TEXT"), ("image_url", "TEXT"),
                          ("url", "TEXT"),
                          ("preordered", "INTEGER NOT NULL DEFAULT 0")]:
            if col not in owned_cols:  # migrate pre-library-tab databases
                c.execute(f"ALTER TABLE owned_books ADD COLUMN {col} {decl}")
        for k, v in DEFAULT_SETTINGS.items():
            c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                      (k, v))


# ---------- settings ----------

def get_settings() -> dict:
    with conn() as c:
        rows = c.execute("SELECT key, value FROM settings").fetchall()
    s = dict(DEFAULT_SETTINGS)
    s.update({r["key"]: r["value"] for r in rows})
    return s


def save_settings(values: dict):
    with conn() as c:
        for k, v in values.items():
            c.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (k, str(v)),
            )


def secret_key() -> str:
    """The Flask session key: random, created once per install."""
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO app_secrets (key, value) "
                  "VALUES ('flask_secret_key', ?)", (secrets.token_hex(32),))
        return c.execute("SELECT value FROM app_secrets "
                         "WHERE key = 'flask_secret_key'").fetchone()[0]


# ---------- watches ----------

def add_watch(wtype: str, name: str, asin: str | None) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO watches (type, name, asin, created_at) "
            "VALUES (?, ?, ?, ?)",
            (wtype, name.strip(), asin, now_iso()),
        )
        if cur.rowcount == 0:
            row = c.execute("SELECT id FROM watches WHERE type=? AND name=?",
                            (wtype, name.strip())).fetchone()
            return row["id"]
        return cur.lastrowid


def remove_watch(watch_id: int):
    with conn() as c:
        c.execute("DELETE FROM watches WHERE id = ?", (watch_id,))


def list_watches() -> list[dict]:
    today = date.today().isoformat()
    with conn() as c:
        rows = c.execute("""
            SELECT w.*,
                   COUNT(wb.asin)                       AS book_count,
                   SUM(wb.is_new)                       AS new_count,
                   MAX(b.release_date)                  AS latest_release,
                   SUM(CASE WHEN b.release_date > ?  -- TBA isn't orderable
                            THEN 1 ELSE 0 END)          AS preorder_count
            FROM watches w
            LEFT JOIN watch_books wb ON wb.watch_id = w.id AND wb.hidden = 0
            LEFT JOIN books b ON b.asin = wb.asin
            GROUP BY w.id
            ORDER BY w.type, w.name COLLATE NOCASE
        """, (today,)).fetchall()
    return [dict(r) for r in rows]


def get_watch(watch_id: int) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM watches WHERE id = ?", (watch_id,)).fetchone()
    return dict(r) if r else None


def mark_watch_checked(watch_id: int, error: str | None = None):
    with conn() as c:
        c.execute("UPDATE watches SET last_checked_at = ?, last_error = ? WHERE id = ?",
                  (now_iso(), error, watch_id))


# ---------- books ----------

_BOOK_UPSERT = """
    INSERT INTO books
    (asin, title, subtitle, authors, narrators, series_title,
     series_sequence, release_date, runtime_minutes, language, image_url,
     url)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(asin) DO UPDATE SET
        title = excluded.title,
        subtitle = excluded.subtitle,
        authors = excluded.authors,
        narrators = excluded.narrators,
        series_title = excluded.series_title,
        series_sequence = excluded.series_sequence,
        release_date = excluded.release_date,
        runtime_minutes = excluded.runtime_minutes,
        language = excluded.language,
        image_url = excluded.image_url,
        url = excluded.url
"""
# published_date is deliberately not refreshed here — it comes from the
# metadata enrichment, not the Audible catalog.


def known_asins(watch_id: int) -> set[str]:
    with conn() as c:
        rows = c.execute("SELECT asin FROM watch_books WHERE watch_id = ?",
                         (watch_id,)).fetchall()
    return {r["asin"] for r in rows}


def upsert_books(watch_id: int, books: list[dict], as_new: bool,
                 hidden: bool = False) -> list[dict]:
    """Record fetched books. Catalog metadata refreshes on every check (a
    TBA placeholder can gain a real release date later); the returned list
    contains only books newly linked to this watch. hidden=True links them
    already hidden and seen (used for the language filter)."""
    existing = known_asins(watch_id)
    inserted = []
    with conn() as c:
        for b in books:
            if not b.get("asin"):
                continue
            c.execute(_BOOK_UPSERT, (
                b["asin"], b.get("title"), b.get("subtitle"),
                b.get("authors"), b.get("narrators"), b.get("series_title"),
                b.get("series_sequence"), b.get("release_date"),
                b.get("runtime_minutes"), b.get("language"),
                b.get("image_url"), b.get("url"),
            ))
            if b["asin"] in existing:
                continue
            c.execute("""
                INSERT OR IGNORE INTO watch_books
                (watch_id, asin, discovered_at, is_new, notified, hidden)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                watch_id, b["asin"], now_iso(),
                1 if (as_new and not hidden) else 0,
                0 if (as_new and not hidden) else 1,
                1 if hidden else 0,
            ))
            inserted.append(b)
            existing.add(b["asin"])
    return inserted


def hide_books(watch_id: int, asins: list[str]):
    """Hide the given catalog entries (e.g. wrong-language editions that were
    imported before a language preference was set)."""
    with conn() as c:
        c.executemany(
            "UPDATE watch_books SET hidden = 1, is_new = 0, notified = 1 "
            "WHERE asin = ? AND watch_id = ?",
            [(a, watch_id) for a in asins])


def books_for_watch(watch_id: int, include_hidden: bool = False) -> list[dict]:
    q = ("SELECT b.*, wb.watch_id, wb.discovered_at, wb.is_new, "
         "wb.notified, wb.hidden "
         "FROM books b JOIN watch_books wb ON wb.asin = b.asin "
         "WHERE wb.watch_id = ? ")
    if not include_hidden:
        q += "AND wb.hidden = 0 "
    q += "ORDER BY (b.release_date IS NULL), b.release_date DESC"
    with conn() as c:
        rows = c.execute(q, (watch_id,)).fetchall()
    return [dict(r) for r in rows]


def dedupe_by_asin(rows) -> list[dict]:
    """One entry per book, keeping list order. When a book matches several
    watches (e.g. its author and its series) the series watch wins the
    attribution."""
    best: dict[str, dict] = {}
    for r in rows:
        r = dict(r)
        cur = best.get(r["asin"])
        if cur is None or (r["watch_type"] == "series"
                           and cur["watch_type"] != "series"):
            best[r["asin"]] = r  # dict keeps first-seen position on replace
    return list(best.values())


def new_books(watch_id: int | None = None) -> list[dict]:
    q = ("SELECT b.*, wb.watch_id, wb.is_new, wb.hidden, "
         "w.name AS watch_name, w.type AS watch_type "
         "FROM books b "
         "JOIN watch_books wb ON wb.asin = b.asin "
         "JOIN watches w ON w.id = wb.watch_id "
         "WHERE wb.is_new = 1 AND wb.hidden = 0")
    args: tuple = ()
    if watch_id is not None:
        q += " AND wb.watch_id = ?"
        args = (watch_id,)
    q += " ORDER BY (b.release_date IS NULL), b.release_date DESC"
    with conn() as c:
        rows = c.execute(q, args).fetchall()
    if watch_id is None:  # dashboard: show each book once
        return dedupe_by_asin(rows)
    return [dict(r) for r in rows]


def books_missing_published(limit: int = 30) -> list[dict]:
    """Visible books whose original publish date hasn't been looked up yet,
    pre-orders and newest releases first. NULL = never looked up; '' = looked
    up, nothing found (won't be retried)."""
    today = date.today().isoformat()
    with conn() as c:
        rows = c.execute("""
            SELECT b.asin, b.title, b.authors
            FROM books b
            WHERE b.published_date IS NULL
              AND EXISTS (SELECT 1 FROM watch_books wb
                          WHERE wb.asin = b.asin AND wb.hidden = 0)
            ORDER BY (b.release_date IS NULL OR b.release_date > ?) DESC,
                     b.release_date DESC
            LIMIT ?
        """, (today, limit)).fetchall()
    return [dict(r) for r in rows]


def set_published_date(asin: str, value: str):
    with conn() as c:
        c.execute("UPDATE books SET published_date = ? WHERE asin = ?",
                  (value, asin))


def books_missing_availability(limit: int = 50) -> list[dict]:
    """Visible books not yet validated against the marketplace's own store
    listing, pre-orders and newest first. TBA books (no release date) are
    excluded — unannounced titles often aren't in the store search yet, so
    "absent" proves nothing; they're checked once they gain a date."""
    today = date.today().isoformat()
    with conn() as c:
        rows = c.execute("""
            SELECT b.asin, b.title, b.authors
            FROM books b
            WHERE b.available IS NULL AND b.title IS NOT NULL
              AND b.release_date IS NOT NULL
              AND EXISTS (SELECT 1 FROM watch_books wb
                          WHERE wb.asin = b.asin AND wb.hidden = 0)
            ORDER BY (b.release_date > ?) DESC, b.release_date DESC
            LIMIT ?
        """, (today, limit)).fetchall()
    return [dict(r) for r in rows]


def set_availability(asin: str, available: bool):
    with conn() as c:
        c.execute("UPDATE books SET available = ? WHERE asin = ?",
                  (1 if available else 0, asin))


def hide_asin_everywhere(asin: str):
    """Hide a book on every watch (e.g. an edition not sold in this
    marketplace). Runs once per book — a manual unhide is respected."""
    with conn() as c:
        c.execute("UPDATE watch_books SET hidden = 1, is_new = 0, "
                  "notified = 1 WHERE asin = ?", (asin,))


def series_sizes() -> dict[str, int]:
    """Known number of distinct catalog entries per series title (from every
    book any watch has surfaced)."""
    with conn() as c:
        rows = c.execute(
            "SELECT series_title, COUNT(1) AS n FROM books "
            "WHERE series_title IS NOT NULL GROUP BY series_title").fetchall()
    return {r["series_title"]: r["n"] for r in rows}


def preorder_books() -> list[dict]:
    """Visible books you can pre-order across all watches, soonest release
    first, deduped when a book matches multiple watches (the series watch wins
    the attribution). TBA entries (no release date) are excluded: Audible
    only opens a store page once a title has a date, so they're dead links.
    A check refreshes release dates, so a TBA book appears here once dated."""
    today = date.today().isoformat()
    with conn() as c:
        rows = c.execute("""
            SELECT b.*, wb.watch_id, wb.is_new,
                   w.name AS watch_name, w.type AS watch_type
            FROM books b
            JOIN watch_books wb ON wb.asin = b.asin
            JOIN watches w ON w.id = wb.watch_id
            WHERE wb.hidden = 0 AND b.release_date > ?
            ORDER BY b.release_date
        """, (today,)).fetchall()
    return dedupe_by_asin(rows)


def unnotified_books() -> list[dict]:
    with conn() as c:
        rows = c.execute("""
            SELECT b.*, wb.watch_id, wb.is_new,
                   w.name AS watch_name, w.type AS watch_type
            FROM books b
            JOIN watch_books wb ON wb.asin = b.asin
            JOIN watches w ON w.id = wb.watch_id
            WHERE wb.notified = 0 AND wb.hidden = 0
            ORDER BY (b.release_date IS NULL), b.release_date DESC
        """).fetchall()
    return [dict(r) for r in rows]


def mark_notified(pairs: list[tuple[str, int]]):
    with conn() as c:
        c.executemany("UPDATE watch_books SET notified = 1 "
                      "WHERE asin = ? AND watch_id = ?", pairs)


def mark_seen(asin: str, watch_id: int):
    """Seen is about the book, so clear it on every watch it matches —
    otherwise its other watch's copy would pop up on the dashboard."""
    with conn() as c:
        c.execute("UPDATE watch_books SET is_new = 0 WHERE asin = ?", (asin,))


def set_book_hidden(asin: str, watch_id: int, hidden: bool):
    """Hide/unhide a catalog entry. The link row is kept (not deleted) so the
    next Audible check doesn't re-import it and flag it as a new release."""
    with conn() as c:
        if hidden:
            c.execute("UPDATE watch_books SET hidden = 1, is_new = 0, "
                      "notified = 1 WHERE asin = ? AND watch_id = ?",
                      (asin, watch_id))
        else:
            c.execute("UPDATE watch_books SET hidden = 0 "
                      "WHERE asin = ? AND watch_id = ?", (asin, watch_id))


def hide_duplicate_books(watch_id: int) -> int:
    """Hide duplicate catalog entries for a watch, keeping one book per
    (series, sequence) — or per title when there is no sequence. An author
    watch spans several series, so book 1 of one series is not a duplicate
    of book 1 of another. An entry with a real release date beats a TBA
    placeholder; then earlier releases win."""
    groups: dict = {}
    for b in books_for_watch(watch_id):
        key = (("seq", (b["series_title"] or "").strip().lower(),
                b["series_sequence"].strip())
               if b["series_sequence"]
               else ("title", (b["title"] or "").strip().lower()))
        groups.setdefault(key, []).append(b)
    hidden = 0
    with conn() as c:
        for grp in groups.values():
            grp.sort(key=lambda b: (b["release_date"] is None,
                                    b["release_date"] or "",
                                    b["discovered_at"]))
            for b in grp[1:]:
                c.execute("UPDATE watch_books SET hidden = 1, is_new = 0, "
                          "notified = 1 WHERE asin = ? AND watch_id = ?",
                          (b["asin"], watch_id))
                hidden += 1
    return hidden


def mark_all_seen(watch_id: int | None = None):
    with conn() as c:
        if watch_id is None:
            c.execute("UPDATE watch_books SET is_new = 0")
        else:
            c.execute("UPDATE watch_books SET is_new = 0 WHERE watch_id = ?",
                      (watch_id,))


# ---------- owned library (from connected Audible account) ----------

def replace_owned(rows: list[dict]):
    """Replace the owned-library snapshot with freshly synced book dicts."""
    with conn() as c:
        c.execute("DELETE FROM owned_books")
        c.executemany("""
            INSERT OR IGNORE INTO owned_books
            (asin, title, authors, narrators, series_title, series_sequence,
             series_asin, release_date, runtime_minutes, language, image_url,
             url, purchase_date, preordered)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [(b["asin"], b.get("title"), b.get("authors"),
               b.get("narrators"), b.get("series_title"),
               b.get("series_sequence"), b.get("series_asin"),
               b.get("release_date"), b.get("runtime_minutes"),
               b.get("language"), b.get("image_url"), b.get("url"),
               b.get("purchase_date"), 1 if b.get("preordered") else 0)
              for b in rows if b.get("asin")])


def owned_books_list() -> list[dict]:
    """The synced library, most recent purchases first."""
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM owned_books WHERE title IS NOT NULL "
            "ORDER BY (purchase_date IS NULL), purchase_date DESC, "
            "title COLLATE NOCASE").fetchall()
    return [dict(r) for r in rows]


def owned_asins() -> set[str]:
    with conn() as c:
        return {r["asin"] for r in
                c.execute("SELECT asin FROM owned_books").fetchall()}


def owned_count() -> int:
    with conn() as c:
        return c.execute("SELECT COUNT(1) FROM owned_books").fetchone()[0]


# ---------- check log ----------

def log_check(trigger: str, new_count: int, errors: str | None, email_sent: bool):
    with conn() as c:
        c.execute(
            "INSERT INTO check_log (ran_at, trigger, new_books, errors, email_sent) "
            "VALUES (?, ?, ?, ?, ?)",
            (now_iso(), trigger, new_count, errors, 1 if email_sent else 0))


def recent_checks(limit: int = 10) -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM check_log ORDER BY id DESC LIMIT ?",
                         (limit,)).fetchall()
    return [dict(r) for r in rows]
