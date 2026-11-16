"""Page behaviour added after the 2026-11 review: background checks, safe
confirm dialogs, per-series numbering, owned editions, TBA badges, and the
author-name warning."""
import json

import app as webapp
import checker
import db


def test_check_now_runs_in_background(client, watched):
    resp = client.post("/check-now")
    assert resp.status_code == 302
    webapp._check_thread.join(timeout=10)
    assert db.recent_checks(1)[0]["trigger"] == "manual"
    assert not webapp._check_lock.locked()


def test_dashboard_refreshes_while_a_check_runs(client):
    assert webapp._check_lock.acquire(blocking=False)
    try:
        html = client.get("/").get_data(as_text=True)
        assert 'http-equiv="refresh"' in html and "Checking…" in html
        resp = client.post("/check-now", follow_redirects=True)
        assert "already running" in resp.get_data(as_text=True)
    finally:
        webapp._check_lock.release()
    assert 'http-equiv="refresh"' not in client.get("/").get_data(as_text=True)


def test_check_failure_releases_the_lock(monkeypatch):
    def boom(trigger):
        raise RuntimeError("boom")
    monkeypatch.setattr(checker, "check_all", boom)
    webapp.scheduled_check()
    assert not webapp._check_lock.locked()


def test_remove_confirm_is_safe_with_quotes(client):
    db.add_watch("series", "Ender's \"Game\"", None)
    html = client.get("/?tab=series").get_data(as_text=True)
    assert "onsubmit=" not in html
    assert 'data-confirm="Stop watching Ender&#39;s &#34;Game&#34;?"' in html


def test_bulk_add_route_is_gone(client):
    assert client.post("/watch/add-bulk", data={}).status_code in (404, 405)


def test_series_watch_shows_its_own_book_number(client, watched):
    """A book in two series shows its number in the watched series."""
    book = {"asin": "B050", "title": "Crossover", "authors": "Test Author",
            "series_title": "Universe", "series_sequence": "12",
            "all_series": [{"title": "Universe", "asin": "UNI", "sequence": "12"},
                           {"title": "Great Series", "asin": "SER123",
                            "sequence": "4"}],
            "release_date": "2025-01-01", "url": "u"}
    db.upsert_books(watched["series"], [book], as_new=False)
    row = next(b for b in db.books_for_watch(watched["series"])
               if b["asin"] == "B050")
    assert (row["series_title"], row["series_sequence"]) == ("Great Series", "4")
    db.upsert_books(watched["author"], [book], as_new=False)
    row = next(b for b in db.books_for_watch(watched["author"])
               if b["asin"] == "B050")
    assert row["series_sequence"] == "12"  # author watch: catalog default
    stored = json.loads(row["all_series"])
    assert len(stored) == 2


def test_other_region_edition_counts_as_owned(client, watched):
    db.replace_owned([{"asin": "UK-B002", "title": "The Second Book",
                       "authors": "Test Author", "url": "u"}])
    html = client.get(f"/watch/{watched['author']}").get_data(as_text=True)
    assert "badge-owned" in html
    assert "you own 1 of" in html


def test_tba_book_gets_tba_badge_not_preorder(client, watched):
    db.upsert_books(watched["series"], [{
        "asin": "BTBA", "title": "Untitled Book 9", "authors": "Test Author",
        "series_title": "Great Series", "series_sequence": "9",
        "release_date": None, "url": "u"}], as_new=False)
    html = client.get(f"/watch/{watched['series']}").get_data(as_text=True)
    card = html[html.index("Untitled Book 9") - 900:html.index("Untitled Book 9")]
    assert "badge-tba" in card and "PRE-ORDER<" not in card


def test_author_with_no_exact_match_is_flagged(client):
    resp = client.post("/watch/add", data={"type": "author",
                                           "name": "Nobody Atall"},
                       follow_redirects=True)
    assert "no titles under exactly that name" in resp.get_data(as_text=True)
    w = db.list_watches()[0]
    assert "exactly this name" in (w["last_error"] or "")
