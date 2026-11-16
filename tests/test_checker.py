"""Release checking, alerting, email digest, and metadata enrichment."""
import checker
import db
import mock_api


def test_baseline_flags_nothing_new(watched):
    assert watched["author_count"] == 3
    assert watched["series_count"] == 2
    assert db.new_books() == []


def test_steady_state_check(watched):
    r = checker.check_all()
    assert r["new_books"] == 0
    assert not r["errors"]


def test_new_release_detection(watched):
    mock_api.state["include_new"] = True
    r = checker.check_all()
    # B004 matches both the author watch and the series watch -> 2 rows
    assert r["new_books"] == 2
    fresh = db.new_books()
    assert {b["asin"] for b in fresh} == {"B004"}
    assert all(b["title"].startswith("The Third Book") for b in fresh)


def test_recheck_does_not_realert(watched):
    mock_api.state["include_new"] = True
    checker.check_all()
    assert checker.check_all()["new_books"] == 0


def test_email_digest(watched):
    mock_api.state["include_new"] = True
    checker.check_all()
    rows = (db.new_books(watched["author"])
            + db.new_books(watched["series"]))
    assert len(rows) == 2
    subject, html, text = checker.build_email(rows)
    assert "2 new books" in subject
    assert "The Third Book" in html
    assert "2099-10-20" in text


def test_email_off_does_not_save_up_a_backlog(watched, monkeypatch):
    """Books found while email is off are on the dashboard; switching email
    on later must not send every one of them in one huge digest."""
    mock_api.state["include_new"] = True
    checker.check_all()  # email off
    assert db.unnotified_books() == []
    sent = []
    monkeypatch.setattr(checker, "send_new_release_email",
                        lambda books, settings: sent.append(books))
    db.save_settings({"email_enabled": "1"})
    checker.check_all()
    assert sent == []


def test_seen_books_are_not_emailed(watched, monkeypatch):
    """A book marked seen before its email went out (say SMTP was failing)
    isn't emailed afterwards."""
    sent, fail = [], [True]

    def send(books, settings):
        if fail[0]:
            raise OSError("smtp down")
        sent.append(books)
    monkeypatch.setattr(checker, "send_new_release_email", send)
    db.save_settings({"email_enabled": "1"})
    mock_api.state["include_new"] = True
    checker.check_all()           # email fails; B004 stays pending
    db.mark_seen("B004")          # user sees it on the dashboard
    fail[0] = False
    checker.check_all()
    assert sent == [] and db.unnotified_books() == []


def test_mark_seen_clears_book_on_all_watches(watched):
    mock_api.state["include_new"] = True
    checker.check_all()
    # seen is per book: clears the author AND series copies
    db.mark_seen("B004")
    assert db.new_books(watched["series"]) == []
    assert db.new_books() == []


def test_mark_all_seen(watched):
    mock_api.state["include_new"] = True
    checker.check_all()
    assert db.new_books()
    db.mark_all_seen()
    assert db.new_books() == []


def test_language_filter_hides_foreign_editions(watched):
    db.save_settings({"language": "english"})
    mock_api.state["include_foreign"] = True
    r = checker.check_all()
    assert r["new_books"] == 0, "german edition must not alert"
    visible = {b["asin"] for b in db.books_for_watch(watched["author"])}
    assert "B005" not in visible
    hidden = {b["asin"] for b in
              db.books_for_watch(watched["author"], include_hidden=True)
              if b["hidden"]}
    assert "B005" in hidden
    assert db.unnotified_books() == []
    assert checker.check_all()["new_books"] == 0, "must not re-alert"


def test_publish_date_enrichment(watched):
    mock_api.state["include_new"] = True
    checker.check_all()
    checker.enrich_published_dates()
    rows = [b for w in db.list_watches() for b in db.books_for_watch(w["id"])]
    b004 = [b for b in rows if b["asin"] == "B004"]
    assert b004
    assert all(b["published_date"] == "2025-05-01" for b in b004)
    # definitive no-match is recorded as '' so it is never retried
    assert all(b["published_date"] == "" for b in rows
               if b["asin"] != "B004")


def test_unavailable_editions_get_hidden(watched, monkeypatch):
    """A book the region-filtered store doesn't list (e.g. a UK edition that
    slipped in via series relationships) is hidden on every watch — once."""
    monkeypatch.setattr(
        checker.audible_api, "check_market_availability",
        lambda asin, title, author=None, marketplace="us": asin != "B002")
    checker.verify_market_availability()
    for wid in (watched["author"], watched["series"]):
        assert "B002" not in {b["asin"] for b in db.books_for_watch(wid)}
        hidden = {b["asin"] for b in db.books_for_watch(wid, include_hidden=True)
                  if b["hidden"]}
        assert "B002" in hidden
    # no false alerts from the hide
    assert db.new_books() == []
    # a manual unhide is respected — the check never runs twice per book
    db.set_book_hidden("B002", watched["author"], False)
    checker.verify_market_availability()
    assert "B002" in {b["asin"] for b in db.books_for_watch(watched["author"])}


def test_availability_unknown_keeps_book(watched, monkeypatch):
    monkeypatch.setattr(
        checker.audible_api, "check_market_availability",
        lambda asin, title, author=None, marketplace="us": None)
    checker.verify_market_availability()
    assert "B001" in {b["asin"] for b in db.books_for_watch(watched["author"])}
    assert db.books_missing_availability() == [], \
        "inconclusive checks must not be retried forever"


def test_metadata_refreshes_on_recheck(watched):
    """A placeholder that later gains a real release date must update."""
    aid = watched["author"]
    db.upsert_books(aid, [{"asin": "B001", "title": "The First Book",
                           "release_date": None}], as_new=True)
    # still linked from baseline; catalog now reports the date again
    checker.check_all()
    b001 = [b for b in db.books_for_watch(aid) if b["asin"] == "B001"][0]
    assert b001["release_date"] == "2024-03-01"
