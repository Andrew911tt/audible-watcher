"""A book matching both an author watch and a series watch appears once in
the combined views (dashboard, pre-orders, email) — attributed to the series —
but still under each watch on that watch's own page."""
import checker
import db
import mock_api

NEW = "B004"  # mock new release: by Test Author, book 3 of Great Series


def test_new_book_listed_once_on_dashboard(watched):
    mock_api.state["include_new"] = True
    checker.check_all()
    fresh = [b for b in db.new_books() if b["asin"] == NEW]
    assert len(fresh) == 1
    assert fresh[0]["watch_type"] == "series"
    # per-watch lists still show it under both watches
    assert any(b["asin"] == NEW for b in db.new_books(watched["author"]))
    assert any(b["asin"] == NEW for b in db.new_books(watched["series"]))


def test_preorder_listed_once(watched):
    mock_api.state["include_new"] = True
    checker.check_all()
    pre = [b for b in db.preorder_books() if b["asin"] == NEW]
    assert len(pre) == 1 and pre[0]["watch_type"] == "series"


def _book(asin, release_date):
    return {"asin": asin, "title": asin, "authors": "Test Author",
            "series_title": "Great Series", "series_sequence": "9",
            "release_date": release_date}


def test_tba_books_are_not_preorders(watched):
    # no release date = Audible placeholder with no store page (dead link)
    db.upsert_books(watched["series"], [_book("BTBA", None)], as_new=False)
    assert not any(b["asin"] == "BTBA" for b in db.preorder_books())
    series = next(w for w in db.list_watches() if w["id"] == watched["series"])
    counted = series["preorder_count"]
    db.upsert_books(watched["series"], [_book("BTBA", "2099-01-01")],
                    as_new=False)  # a later check finds the real date
    assert any(b["asin"] == "BTBA" for b in db.preorder_books())
    series = next(w for w in db.list_watches() if w["id"] == watched["series"])
    assert series["preorder_count"] == counted + 1


def test_email_lists_book_once_but_marks_all_notified(watched, monkeypatch):
    sent = []
    monkeypatch.setattr(checker, "send_new_release_email",
                        lambda books, settings: sent.append(books))
    db.save_settings({"email_enabled": "1"})
    mock_api.state["include_new"] = True
    checker.check_all()
    assert [b["asin"] for b in sent[0]].count(NEW) == 1
    assert db.unnotified_books() == []  # both watch links marked notified
