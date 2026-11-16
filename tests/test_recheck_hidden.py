"""Books hidden as "not sold in your store" are re-checked weekly: one that
goes on sale later comes back as a new book. Your own hide/unhide choices
are never undone."""
from datetime import datetime, timedelta, timezone

import pytest

import audible_api
import checker
import db
import mock_api


@pytest.fixture
def store(monkeypatch):
    """Which ASINs the store search says are sold here; records lookups."""
    state = {"sold": set(), "asked": [], "down": False}

    def check(asin, title, author=None, marketplace="us"):
        state["asked"].append(asin)
        if state["down"]:
            raise audible_api.AudibleUnavailable("HTTP 503")
        return asin in state["sold"]
    monkeypatch.setattr(audible_api, "check_market_availability", check)
    return state


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(checker, "send_new_release_email",
                        lambda books, settings: sent.append(books))
    db.save_settings({"email_enabled": "1"})
    return sent


def _hide_as_not_sold(watched, store):
    """B002 is judged "not sold here" and hidden on both watches."""
    store["sold"] = {"B001", "B003"}
    checker.verify_market_availability()
    for wid in (watched["author"], watched["series"]):
        assert "B002" not in {b["asin"] for b in db.books_for_watch(wid)}


def _age_last_check(asin, days=8):
    when = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    with db.conn() as c:
        c.execute("UPDATE books SET available_checked_at = ? WHERE asin = ?",
                  (when, asin))


def test_book_that_goes_on_sale_comes_back_as_new(watched, store, outbox):
    _hide_as_not_sold(watched, store)
    store["sold"].add("B002")            # the store starts selling it
    _age_last_check("B002")
    checker.check_all()
    for wid in (watched["author"], watched["series"]):
        assert "B002" in {b["asin"] for b in db.books_for_watch(wid)}
    assert {b["asin"] for b in db.new_books()} == {"B002"}
    assert [b["asin"] for b in outbox[-1]] == ["B002"]


def test_still_not_sold_stays_hidden_and_waits_a_week(watched, store,
                                                      outbox):
    _hide_as_not_sold(watched, store)
    _age_last_check("B002")
    store["asked"].clear()
    checker.check_all()
    assert store["asked"].count("B002") == 1
    assert "B002" not in {b["asin"] for b in
                          db.books_for_watch(watched["author"])}
    store["asked"].clear()
    checker.check_all()                  # checked moments ago: not again
    assert "B002" not in store["asked"]
    assert outbox == []


def test_a_book_you_unhid_is_never_rehidden(watched, store):
    _hide_as_not_sold(watched, store)
    db.set_book_hidden("B002", watched["author"], False)   # your choice
    _age_last_check("B002")
    checker.check_all()
    assert "B002" in {b["asin"] for b in db.books_for_watch(watched["author"])}
    # the other watch's copy is still auto-hidden and still re-checked
    assert "B002" in store["asked"]


def test_a_book_you_hid_is_never_brought_back(watched, store, outbox):
    db.set_book_hidden("B003", watched["author"], True)    # your choice
    store["sold"] = {"B001", "B002", "B003"}
    checker.check_all()
    assert "B003" not in {b["asin"] for b in
                          db.books_for_watch(watched["author"])}
    assert db.books_to_recheck() == []


def test_store_outage_leaves_it_for_next_time(watched, store):
    _hide_as_not_sold(watched, store)
    _age_last_check("B002")
    store["down"] = True
    assert checker.recheck_not_sold_here("us") == 0
    assert [r["asin"] for r in db.books_to_recheck()] == ["B002"]


def test_upgrade_marks_earlier_auto_hidden_books(watched, store):
    _hide_as_not_sold(watched, store)
    with db.conn() as c:                 # a database from before the flag
        c.execute("ALTER TABLE watch_books DROP COLUMN region_hidden")
        c.execute("ALTER TABLE books DROP COLUMN available_checked_at")
    db.init_db()
    assert [r["asin"] for r in db.books_to_recheck()] == ["B002"]


def test_marketplace_change_clears_the_verdicts(client, watched, store):
    _hide_as_not_sold(watched, store)
    client.post("/settings", data={"check_hour": "8", "marketplace": "uk"})
    assert "B002" in {b["asin"] for b in db.books_for_watch(watched["author"])}
    assert db.books_to_recheck() == []


def test_watch_page_says_why_a_book_is_hidden(client, watched, store):
    _hide_as_not_sold(watched, store)
    db.set_book_hidden("B003", watched["author"], True)
    html = client.get(f"/watch/{watched['author']}").get_data(as_text=True)
    hidden = html[html.index("Hidden entries"):]
    assert "not sold in your Audible store" in hidden
    assert "checked again weekly" in hidden
    assert "hidden by you" in hidden
