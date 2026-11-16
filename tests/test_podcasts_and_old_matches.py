"""Author watches hold the author's audiobooks only: podcasts are skipped,
and entries from before exact author matching (e.g. "Scott Meyers" on a
"Scott Meyer" watch) are hidden on the next check."""
import pytest

import checker
import db
import mock_api
from mock_api import make_product


def podcast(asin, title, author):
    return dict(make_product(asin, title, [author], "2024-04-16"),
                content_type="Podcast", content_delivery_type="PodcastParent")


SCOTT_MEYER = [
    make_product("SM1", "Off to Be the Wizard", ["Scott Meyer"],
                 "2014-03-18", "Magic 2.0", "1", "MAG2"),
    make_product("SM2", "Brute Force", ["Scott Meyer"], "2023-04-10"),
    make_product("SM3", "Navigating Social Media",
                 ["Scott Meyer", "Sarah Carnes"], "2014-02-21"),
    podcast("POD-ED3", "Ed3 Podcast", "Scott Meyer"),
    podcast("POD-AA", "Ageless Alpha", "Scott Meyers"),
]


@pytest.fixture
def scott(monkeypatch):
    monkeypatch.setattr(mock_api, "CATALOG", mock_api.CATALOG + SCOTT_MEYER)
    wid = db.add_watch("author", "Scott Meyer", None)
    checker.baseline_watch(wid)
    return wid


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(checker, "send_new_release_email",
                        lambda books, settings: sent.append(books))
    db.save_settings({"email_enabled": "1"})
    return sent


def _visible(wid):
    return {b["asin"] for b in db.books_for_watch(wid)}


def test_podcasts_are_never_added(scott, outbox):
    checker.check_all()
    assert _visible(scott) == {"SM1", "SM2", "SM3"}
    assert not any(b["asin"].startswith("POD") for batch in outbox
                   for b in batch)


def test_old_wrong_author_and_podcast_entries_get_hidden(scott, outbox):
    # what a watch from before 1.0.2 / 1.0.6 still holds
    db.upsert_books(scott, [
        {"asin": "POD-AA", "title": "Ageless Alpha", "authors": "Scott Meyers",
         "release_date": "2024-04-16", "url": "u"},
        {"asin": "POD-RC", "title": "The Right Cast Podcast",
         "authors": "Ashley Scott Meyers", "release_date": "2021-07-14",
         "url": "u"},
        {"asin": "POD-ED3", "title": "Ed3 Podcast", "authors": "Scott Meyer",
         "release_date": "2023-03-25", "url": "u"},
    ], as_new=True)
    checker.check_all()
    assert _visible(scott) == {"SM1", "SM2", "SM3"}
    hidden = {b["asin"]: b for b in db.books_for_watch(scott,
                                                       include_hidden=True)
              if b["hidden"]}
    assert {"POD-AA", "POD-RC", "POD-ED3"} <= set(hidden)
    assert all(hidden[a]["mismatch_hidden"]
               for a in ("POD-AA", "POD-RC", "POD-ED3"))
    assert not any(b["asin"].startswith("POD") for batch in outbox
                   for b in batch)
    assert db.new_books() == []


def test_unhiding_one_keeps_it(scott, client):
    db.upsert_books(scott, [{"asin": "POD-AA", "title": "Ageless Alpha",
                             "authors": "Scott Meyers",
                             "release_date": "2024-04-16", "url": "u"}],
                    as_new=False)
    checker.check_all()
    db.set_book_hidden("POD-AA", scott, False)   # you want it after all
    checker.check_all()
    assert "POD-AA" in _visible(scott)


def test_watch_page_says_why(scott, client):
    db.upsert_books(scott, [{"asin": "POD-AA", "title": "Ageless Alpha",
                             "authors": "Scott Meyers",
                             "release_date": "2024-04-16", "url": "u"}],
                    as_new=False)
    checker.check_all()
    html = client.get(f"/watch/{scott}").get_data(as_text=True)
    assert "not by Scott Meyer, or a podcast" in html


def test_series_watches_are_left_alone(watched, monkeypatch):
    """Only author watches check credits: a series has many authors."""
    sid = watched["series"]
    db.upsert_books(sid, [{"asin": "GUEST", "title": "Guest Story",
                           "authors": "Someone Else",
                           "series_title": "Great Series",
                           "series_sequence": "9",
                           "release_date": "2020-01-01", "url": "u"}],
                    as_new=False)
    monkeypatch.setattr(mock_api, "CATALOG", mock_api.CATALOG + [
        make_product("GUEST", "Guest Story", ["Someone Else"], "2020-01-01",
                     "Great Series", "9", "SER123")])
    checker.check_all()
    assert "GUEST" in _visible(sid)


def test_search_offers_no_podcasts(client, monkeypatch):
    monkeypatch.setattr(mock_api, "CATALOG", mock_api.CATALOG + SCOTT_MEYER)
    html = client.get("/search?q=Scott+Meyer").get_data(as_text=True)
    assert "Scott Meyers" not in html            # only a podcast author
    assert 'value="Scott Meyer"' in html         # the real author is offered
