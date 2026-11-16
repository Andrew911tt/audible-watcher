"""Hide/unhide semantics and duplicate cleanup."""
import checker
import db


def test_hidden_books_never_resurrect_or_alert(watched):
    aid = watched["author"]
    db.set_book_hidden("B001", aid, True)
    assert "B001" not in {b["asin"] for b in db.books_for_watch(aid)}
    r = checker.check_all()
    assert r["new_books"] == 0, "hidden book must not be re-imported as new"
    assert all(not (b["asin"] == "B001" and b["watch_id"] == aid)
               for b in db.unnotified_books())
    db.set_book_hidden("B001", aid, False)
    assert "B001" in {b["asin"] for b in db.books_for_watch(aid)}


def test_duplicate_cleanup_keeps_dated_entry(watched):
    aid = watched["author"]
    dup = {"asin": "B001DUP", "title": "The First Book (Placeholder)",
           "authors": "Test Author", "narrators": "Test Narrator",
           "series_title": "Great Series", "series_sequence": "1",
           "release_date": None,
           "url": "https://example.com/B001DUP"}
    db.upsert_books(aid, [dup], as_new=True)
    assert db.hide_duplicate_books(aid) == 1
    visible = {b["asin"] for b in db.books_for_watch(aid)}
    assert "B001" in visible and "B001DUP" not in visible
    hidden = [b for b in db.books_for_watch(aid, include_hidden=True)
              if b["hidden"]]
    assert {b["asin"] for b in hidden} == {"B001DUP"}
    assert db.new_books(aid) == [], "hidden duplicate must not stay flagged"
    assert db.hide_duplicate_books(aid) == 0, "second pass finds nothing"


def test_hidden_state_is_per_watch(watched):
    """Hiding a book on the author watch must not hide it on the series
    watch — per-watch state lives in watch_books, metadata is shared."""
    db.set_book_hidden("B001", watched["author"], True)
    series_visible = {b["asin"]
                      for b in db.books_for_watch(watched["series"])}
    assert "B001" in series_visible
