"""App updates that change what's fetched must not flood the user with "new"
books (2026-11-15: widening author watches from 100 to 300 titles flagged and
emailed 113 old Brandon Sanderson and Lee Child books). Books genuinely added
to Audible must still alert, whatever their release date."""
import pytest

import audible_api
import checker
import db
import mock_api
from mock_api import make_product


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(checker, "send_new_release_email",
                        lambda books, settings: sent.append(books))
    db.save_settings({"email_enabled": "1"})
    return sent


def _old_titles(n, start=100):
    return [make_product(f"OLD{start + i}", f"Old Title {start + i}",
                         ["Test Author"], f"20{10 + i % 10:02d}-01-01")
            for i in range(n)]


def _as_previous_version(watch_id):
    with db.conn() as c:
        c.execute("UPDATE watches SET fetch_rev = 1 WHERE id = ?",
                  (watch_id,))


def test_wider_fetch_after_update_is_quiet(watched, outbox, monkeypatch):
    """Replays tonight: an update makes the watch see 60 older titles, while
    one real new release also lands in the same check."""
    aid = watched["author"]
    _as_previous_version(aid)
    monkeypatch.setattr(mock_api, "CATALOG", mock_api.CATALOG + _old_titles(60))
    mock_api.state["include_new"] = True        # B004: a real pre-order
    result = checker.check_all()
    assert {b["asin"] for b in db.new_books()} == {"B004"}
    assert [b["asin"] for b in outbox[0]] == ["B004"]
    assert result["new_books"] == 2             # B004 on author + series
    assert db.get_watch(aid)["fetch_rev"] == audible_api.FETCH_REVISION
    # the older titles are on the watch page, just not flagged
    titles = {b["asin"] for b in db.books_for_watch(aid)}
    assert {"OLD100", "OLD159"} <= titles


def test_recent_release_still_alerts_during_the_update(watched, outbox,
                                                       monkeypatch):
    aid = watched["author"]
    _as_previous_version(aid)
    with db.conn() as c:
        c.execute("UPDATE watches SET last_checked_at = '2026-11-10T08:00:00Z'"
                  " WHERE id = ?", (aid,))
    monkeypatch.setattr(mock_api, "CATALOG", mock_api.CATALOG + [
        make_product("FRESH", "Just Out", ["Test Author"], "2026-11-12"),
        make_product("STALE", "Long Out", ["Test Author"], "2015-06-01")])
    checker.check_all()
    assert {b["asin"] for b in db.new_books()} == {"FRESH"}


def test_after_the_update_old_books_new_to_audible_alert(watched, outbox,
                                                         monkeypatch):
    """Once a watch runs the current fetch, anything that appears is new to
    Audible: an old book added to Audible today is a new book for you."""
    checker.check_all()                          # watch now on current rev
    monkeypatch.setattr(mock_api, "CATALOG", mock_api.CATALOG + [
        make_product("BACKLIST", "First Novel (1998)", ["Test Author"],
                     "1998-04-01")])
    checker.check_all()
    assert {b["asin"] for b in db.new_books()} == {"BACKLIST"}
    assert [b["asin"] for b in outbox[-1]] == ["BACKLIST"]


def test_upgraded_database_starts_on_the_old_revision(watched):
    with db.conn() as c:
        c.execute("ALTER TABLE watches DROP COLUMN fetch_rev")
    db.init_db()
    assert all(w["fetch_rev"] == 0 for w in db.list_watches())


def test_new_watches_start_on_the_current_revision(watched):
    assert db.get_watch(watched["author"])["fetch_rev"] == \
        audible_api.FETCH_REVISION


def test_burst_from_one_watch_is_summarised_in_email(watched, outbox,
                                                     monkeypatch):
    checker.check_all()                          # current rev
    monkeypatch.setattr(mock_api, "CATALOG", mock_api.CATALOG + _old_titles(12))
    mock_api.state["include_new"] = True
    checker.check_all()
    assert len(db.new_books()) == 13             # all on the dashboard
    sent = outbox[-1]
    summaries = [b for b in sent if b.get("bulk_count")]
    assert summaries == [{"bulk_count": 12, "watch_name": "Test Author",
                          "watch_type": "author"}]
    assert [b["asin"] for b in sent if not b.get("bulk_count")] == ["B004"]
    subject, body, text = checker.build_email(sent)
    assert "13 new books" in subject
    assert "12 titles" in body and "12 titles" in text
    assert "Old Title" not in body


def test_small_batches_are_listed_normally(watched, outbox, monkeypatch):
    checker.check_all()
    monkeypatch.setattr(mock_api, "CATALOG", mock_api.CATALOG + _old_titles(
        checker.BULK_EMAIL_LIMIT))
    checker.check_all()
    assert not any(b.get("bulk_count") for b in outbox[-1])
    assert len(outbox[-1]) == checker.BULK_EMAIL_LIMIT


def test_author_watch_fetches_everything(monkeypatch):
    seen = {}

    def fake(name, marketplace="us", max_results=100, strict=False,
             sort=None):
        seen["max"] = max_results
        return []
    monkeypatch.setattr(audible_api, "search_by_author", fake)
    audible_api.fetch_watch_books("author", "X", None, "us")
    assert seen["max"] >= 1000
