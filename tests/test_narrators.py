"""Narrator watches (1.1.0): fetching a narrator's titles, the watches-table
migration, alert rules, and the narrator parts of the UI."""
import re
import sqlite3

import pytest

import audible_api
import checker
import db
import mock_api
from mock_api import make_product

FANTASY = [["Science Fiction & Fantasy", "Fantasy"]]
ROMANCE = [["Romance", "Fantasy"]]

RAY = [
    make_product("R1", "Project Hail Mary", ["Andy Weir"], "2021-05-04",
                 narrators=["Ray Porter"], genres=FANTASY),
    make_product("R2", "The Ensemble", ["Some Writer"], "2023-02-02",
                 narrators=["Ray Porter", "Jane Cast", "Bob Cast"]),
    make_product("R3", "Short One", ["Other Writer"], "2022-01-01",
                 narrators=["Ray Porter"], minutes=95, genres=ROMANCE),
    make_product("DP1", "Davina Reads", ["Someone"], "2024-01-01",
                 narrators=["Davina Porter"]),
    dict(make_product("RPOD", "Ray's Podcast", ["Ray Porter"], "2024-04-01",
                      narrators=["Ray Porter"]),
         content_type="Podcast"),
]
RAY_NEW = make_product("R9", "Brand New Narration", ["Andy Weir"],
                       "2099-01-01", narrators=["Ray Porter"],
                       genres=FANTASY)


@pytest.fixture
def ray(monkeypatch):
    monkeypatch.setattr(mock_api, "CATALOG", mock_api.CATALOG + RAY)
    wid = db.add_watch("narrator", "Ray Porter", None)
    checker.baseline_watch(wid)
    return wid


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(checker, "send_new_release_email",
                        lambda books, settings: sent.append(books))
    db.save_settings({"email_enabled": "1", "smtp_user": "u",
                      "smtp_password": "p"})
    return sent


def _visible(wid):
    return {b["asin"] for b in db.books_for_watch(wid)}


# ------------------------------------------------------------- API layer

def test_narrator_query_and_strict_match(monkeypatch):
    seen = []
    real = mock_api.fake_get

    def spy(marketplace, params):
        seen.append(dict(params))
        return real(marketplace, params)
    monkeypatch.setattr(audible_api, "_get", spy)
    monkeypatch.setattr(mock_api, "CATALOG", mock_api.CATALOG + RAY)
    books = audible_api.fetch_watch_books("narrator", "Ray Porter", None)
    # the narrator parameter itself (Audible ignores unknown parameters and
    # would return the whole catalog), newest first
    assert seen[0]["narrator"] == "Ray Porter"
    assert seen[0]["products_sort_by"] == "-ReleaseDate"
    asins = {b["asin"] for b in books}
    assert {"R1", "R2", "R3"} <= asins
    assert "DP1" not in asins                   # Davina Porter isn't Ray


def test_name_variants_count_as_one_narrator():
    assert audible_api.same_narrator("R. C. Bray", "R.C. Bray")
    assert audible_api.same_narrator("Kevin R. Free", "Kevin Free")
    assert not audible_api.same_narrator("Ray Porter", "Davina Porter")
    assert not audible_api.same_narrator("Porter", "Ray Porter")
    assert audible_api.credits_narrator(["Ray Porter & Jane Cast"],
                                        "Ray Porter")


def test_fetch_pages_through_every_title(monkeypatch):
    many = [make_product(f"P{i:03}", f"Book {i}", ["A"], "2020-01-01",
                         narrators=["Prolific Reader"]) for i in range(120)]

    def paged(marketplace, params):
        assert params.get("narrator") == "Prolific Reader"
        page, num = int(params["page"]), int(params["num_results"])
        return {"products": many[page * num:(page + 1) * num]}
    monkeypatch.setattr(audible_api, "_get", paged)
    books = audible_api.fetch_watch_books("narrator", "Prolific Reader", None)
    assert len(books) == 120


def test_middle_initial_names_are_also_searched_without_it(monkeypatch):
    """Audible's search for "Kevin R. Free" misses "Kevin Free" credits."""
    full = make_product("KF1", "Full Name", ["A"], "2021-01-01",
                        narrators=["Kevin R. Free"])
    short = make_product("KF2", "Short Name", ["A"], "2023-01-01",
                         narrators=["Kevin Free"])
    other = make_product("KF3", "Not Him", ["A"], "2022-01-01",
                         narrators=["Kevin Freeman"])
    queries = []

    def exact(marketplace, params):
        queries.append(params["narrator"])
        want = params["narrator"].lower()
        hits = [p for p in (full, short, other)
                if any(n["name"].lower() == want for n in p["narrators"])]
        return {"products": hits if int(params["page"]) == 0 else []}
    monkeypatch.setattr(audible_api, "_get", exact)
    books = audible_api.fetch_watch_books("narrator", "Kevin R. Free", None)
    assert queries == ["Kevin R. Free", "Kevin Free"]
    assert [b["asin"] for b in books] == ["KF2", "KF1"]   # newest first
    queries.clear()
    audible_api.fetch_watch_books("narrator", "Ray Porter", None)
    assert queries == ["Ray Porter"]                      # one query


def test_genres_come_from_category_ladders():
    book = audible_api._simplify(make_product(
        "G1", "T", ["A"], "2020-01-01",
        genres=[["Science Fiction & Fantasy", "Fantasy", "Epic"],
                ["Science Fiction & Fantasy", "Fantasy", "Humorous"]]), "us")
    assert book["genres"] == ["Science Fiction & Fantasy",
                              "Science Fiction & Fantasy › Fantasy"]


# ------------------------------------------------------------- migration

OLD_WATCHES = """
CREATE TABLE watches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    type            TEXT NOT NULL CHECK (type IN ('author', 'series')),
    name            TEXT NOT NULL,
    asin            TEXT,
    created_at      TEXT NOT NULL,
    last_checked_at TEXT,
    last_error      TEXT,
    UNIQUE (type, name)
);
CREATE TABLE books (asin TEXT PRIMARY KEY, title TEXT, authors TEXT,
                    narrators TEXT, release_date TEXT);
CREATE TABLE watch_books (
    watch_id      INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
    asin          TEXT NOT NULL REFERENCES books(asin),
    discovered_at TEXT NOT NULL,
    is_new        INTEGER NOT NULL DEFAULT 0,
    notified      INTEGER NOT NULL DEFAULT 0,
    hidden        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (asin, watch_id)
);
"""


def test_old_database_migrates_and_keeps_everything(tmp_path, monkeypatch):
    path = str(tmp_path / "old.db")
    c = sqlite3.connect(path)
    c.executescript(OLD_WATCHES)
    c.execute("INSERT INTO watches VALUES (1, 'author', 'A', NULL, 't', "
              "'2026-01-01T00:00:00Z', NULL)")
    c.execute("INSERT INTO watches VALUES (5, 'series', 'S', 'SX', 't', "
              "NULL, 'oops')")
    c.execute("INSERT INTO watches VALUES (9, 'author', 'Gone', NULL, 't', "
              "NULL, NULL)")
    c.execute("DELETE FROM watches WHERE id = 9")   # sqlite_sequence = 9
    c.execute("INSERT INTO books VALUES ('X1', 'Book', 'A', 'N', "
              "'2020-01-01')")
    c.execute("INSERT INTO watch_books VALUES (1, 'X1', 't', 1, 0, 0)")
    c.execute("INSERT INTO watch_books VALUES (5, 'X1', 't', 0, 1, 1)")
    c.commit()
    c.close()

    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    db.init_db()  # running again is harmless

    watches = {w["id"]: w for w in db.list_watches()}
    assert set(watches) == {1, 5}
    assert watches[5]["asin"] == "SX" and watches[5]["last_error"] == "oops"
    assert watches[1]["baselined_at"]               # had books: baselined
    links = {(b["watch_id"], b["is_new"], b["notified"], b["hidden"])
             for wid in (1, 5)
             for b in db.books_for_watch(wid, include_hidden=True)}
    assert links == {(1, 1, 0, 0), (5, 0, 1, 1)}
    nid = db.add_watch("narrator", "Ray Porter", None)
    assert nid == 10                                # removed ids not reused
    db.remove_watch(1)                              # cascade still works
    with db.conn() as c:
        assert c.execute("SELECT COUNT(1) FROM watch_books "
                         "WHERE watch_id = 1").fetchone()[0] == 0
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


# ------------------------------------------------------------- behaviour

def test_baseline_is_quiet_and_strict(ray, outbox):
    assert _visible(ray) == {"R1", "R2", "R3"}      # no Davina, no podcast
    checker.check_all()
    assert outbox == [] and db.new_books() == []


def test_new_narration_alerts_once(ray, outbox, monkeypatch):
    monkeypatch.setattr(mock_api, "CATALOG", mock_api.CATALOG + [RAY_NEW])
    checker.check_all()
    checker.check_all()
    assert [[b["asin"] for b in batch] for batch in outbox] == [["R9"]]
    assert outbox[0][0]["watch_type"] == "narrator"


def test_late_narrator_credit_is_not_new_again(outbox, monkeypatch):
    """The author's watch found and emailed the book; Audible credits the
    narrator weeks later. The narrator watch records it quietly."""
    uncredited = make_product("L1", "Late Credit", ["Andy Weir"],
                              "2099-02-02", narrators=["TBD"])
    monkeypatch.setattr(mock_api, "CATALOG", list(RAY))
    aid = db.add_watch("author", "Andy Weir", None)
    nid = db.add_watch("narrator", "Ray Porter", None)
    checker.baseline_watch(aid)
    checker.baseline_watch(nid)
    monkeypatch.setattr(mock_api, "CATALOG", RAY + [uncredited])
    checker.check_all()
    assert [b["asin"] for b in outbox[0]] == ["L1"]
    credited = dict(uncredited, narrators=[{"name": "Ray Porter"}])
    monkeypatch.setattr(mock_api, "CATALOG", RAY + [credited])
    checker.check_all()
    assert len(outbox) == 1                         # not emailed again
    assert "L1" in _visible(nid)                    # but listed
    assert db.new_books(nid) == []


def test_same_check_both_watches_attribute_to_author(outbox, monkeypatch):
    monkeypatch.setattr(mock_api, "CATALOG", list(RAY))
    aid = db.add_watch("author", "Andy Weir", None)
    nid = db.add_watch("narrator", "Ray Porter", None)
    checker.baseline_watch(aid)
    checker.baseline_watch(nid)
    monkeypatch.setattr(mock_api, "CATALOG", RAY + [RAY_NEW])
    checker.check_all()
    assert [(b["asin"], b["watch_type"]) for b in outbox[0]] == [
        ("R9", "author")]


def test_attribution_order():
    rows = [{"asin": "X", "watch_type": t, "watch_name": t}
            for t in ("narrator", "author", "series")]
    assert db.dedupe_by_asin(rows)[0]["watch_type"] == "series"
    assert db.dedupe_by_asin(rows[:2])[0]["watch_type"] == "author"


def test_other_languages_are_hidden(monkeypatch):
    monkeypatch.setattr(mock_api, "CATALOG", RAY + [make_product(
        "RDE", "Auf Deutsch", ["A"], "2020-01-01", language="german",
        narrators=["Ray Porter"])])
    db.save_settings({"language": "english"})
    wid = db.add_watch("narrator", "Ray Porter", None)
    checker.baseline_watch(wid)
    assert "RDE" not in _visible(wid)


def test_wrong_narrator_entries_get_hidden(ray, client):
    db.upsert_books(ray, [{"asin": "DP1", "title": "Davina Reads",
                           "authors": "Someone", "narrators": "Davina Porter",
                           "release_date": "2024-01-01", "url": "u"}],
                    as_new=True)
    checker.check_all()
    assert "DP1" not in _visible(ray)
    html = client.get(f"/watch/{ray}").get_data(as_text=True)
    assert "not narrated by Ray Porter, or a podcast" in html


def test_bulk_narrations_collapse_in_email(ray, outbox, monkeypatch):
    burst = [make_product(f"BK{i}", f"Burst {i}", ["A"], "2099-03-03",
                          narrators=["Ray Porter"]) for i in range(12)]
    monkeypatch.setattr(mock_api, "CATALOG", mock_api.CATALOG + burst)
    checker.check_all()
    assert outbox[0] == [{"bulk_count": 12, "watch_name": "Ray Porter",
                          "watch_type": "narrator"}]


def test_email_row_names_a_single_narrator():
    one = checker._book_row_html({
        "title": "T", "authors": "A", "narrators": "Ray Porter", "url": "u",
        "watch_name": "A", "watch_type": "author"})
    cast = checker._book_row_html({
        "title": "T", "authors": "A", "narrators": "X Y, Z W", "url": "u",
        "watch_name": "A", "watch_type": "author"})
    assert "narrated by Ray Porter" in one
    assert "narrated by" not in cast


# ------------------------------------------------------------- UI

def test_search_for_the_short_name_finds_the_full_one(client, monkeypatch):
    """"Kevin Free" isn't part of "Kevin R. Free", but it's him."""
    import app as webapp
    monkeypatch.setattr(mock_api, "CATALOG", mock_api.CATALOG + [
        make_product("K1", "One", ["A"], "2020-01-01",
                     narrators=["Kevin R. Free"])])
    with webapp.app.test_request_context():
        found = webapp._narrator_matches("Kevin Free", "us", "")
    assert [p["name"] for p in found] == []         # mock: loose substring
    with webapp.app.test_request_context():
        found = webapp._narrator_matches("Kevin", "us", "")
    assert [p["name"] for p in found] == ["Kevin R. Free"]
    monkeypatch.setattr(webapp.audible_api, "search_by_narrator",
                        lambda *a, **k: [audible_api._simplify(
                            mock_api.CATALOG[-1], "us")])
    with webapp.app.test_request_context():
        found = webapp._narrator_matches("Kevin Free", "us", "")
    assert [p["name"] for p in found] == ["Kevin R. Free"]


def test_narrator_only_search_lists_narrators_before_series(client,
                                                            monkeypatch):
    monkeypatch.setattr(mock_api, "CATALOG", mock_api.CATALOG + RAY)
    html = client.get("/search?q=Ray+Porter").get_data(as_text=True)
    assert "Author matches" not in html
    assert html.index("Narrator matches") < html.index("Series matches") \
        if "Series matches" in html else "Narrator matches" in html
    html = client.get("/search?q=Test+Author").get_data(as_text=True)
    assert "Narrator matches" not in html           # authors only


def test_search_offers_narrators_with_variants(client, monkeypatch):
    monkeypatch.setattr(mock_api, "CATALOG", mock_api.CATALOG + [
        make_product("K1", "One", ["A"], "2020-01-01",
                     narrators=["Kevin R. Free"]),
        make_product("K2", "Two", ["A"], "2020-01-01",
                     narrators=["Kevin R. Free"]),
        make_product("K3", "Three", ["A"], "2020-01-01",
                     narrators=["Kevin Free"])])
    html = client.get("/search?q=Kevin").get_data(as_text=True)
    assert "Narrator matches" in html
    assert 'value="Kevin R. Free"' in html
    assert "also credited as\n          Kevin Free" in html
    assert 'value="Kevin Free"' not in html         # one person, one row


def test_watch_narrator_and_its_variant(client, monkeypatch):
    monkeypatch.setattr(mock_api, "CATALOG", mock_api.CATALOG + RAY)
    r = client.post("/watch/add", data={"type": "narrator",
                                         "name": "Ray Porter"})
    assert r.status_code == 302
    wid = db.find_watch("narrator", "Ray Porter")
    assert _visible(wid) == {"R1", "R2", "R3"}
    r = client.post("/watch/add", data={"type": "narrator",
                                         "name": "Ray A. Porter"},
                    headers={"X-Requested-With": "fetch"})
    assert r.get_json()["watch_url"].endswith(f"/watch/{wid}")
    assert len([w for w in db.list_watches()
                if w["type"] == "narrator"]) == 1
    # search shows it as watched
    html = client.get("/search?q=Ray+Porter").get_data(as_text=True)
    assert f'href="/watch/{wid}">Watching ✓' in html


def test_dashboard_narrators_tab(ray, client):
    html = client.get("/?tab=narrators").get_data(as_text=True)
    assert "pill-narrator" in html and "Ray Porter" in html
    empty = db.remove_watch(ray) or client.get("/?tab=narrators")
    assert "No narrators watched yet" in empty.get_data(as_text=True)


def test_narrator_line_single_vs_cast(ray, client):
    html = client.get(f"/watch/{ray}").get_data(as_text=True)
    # watch page: every narrator, linked to the watch page or a search
    assert f'class="narrator-link" href="/watch/{ray}">Ray Porter' in html
    assert ">Jane Cast</a>" in html
    assert "/search?q=Jane+Cast&amp;type=narrator" in html
    # a page with every kind of card: the cast list stays off
    db.upsert_books(ray, [{"asin": "R2", "title": "The Ensemble",
                           "narrators": "Ray Porter, Jane Cast, Bob Cast",
                           "release_date": "2099-01-01", "url": "u"},
                          {"asin": "R1", "title": "Project Hail Mary",
                           "narrators": "Ray Porter",
                           "release_date": "2099-01-02", "url": "u"}],
                    as_new=False)
    html = client.get("/?tab=preorders").get_data(as_text=True)
    assert ">Jane Cast</a>" not in html
    assert ">Ray Porter</a>" in html


def test_preorders_from_narrators_section(ray, client, monkeypatch):
    monkeypatch.setattr(mock_api, "CATALOG", mock_api.CATALOG + [RAY_NEW])
    checker.check_all()
    html = client.get("/?tab=preorders").get_data(as_text=True)
    assert "From narrators you&#39;re following" in html


def test_library_by_narrator(client):
    db.replace_owned([
        {"asin": "O1", "title": "Owned One", "authors": "A",
         "narrators": "Ray Porter", "release_date": "2020-01-01"},
        {"asin": "O2", "title": "Owned Two", "authors": "B",
         "narrators": "Kate Reading, Michael Kramer",
         "release_date": "2021-01-01"}])
    html = client.get("/?tab=library&view=narrator").get_data(as_text=True)
    assert "Ray Porter" in html and "Kate Reading" in html
    assert "+ Watch narrator" in html


def test_library_by_narrator_merges_spellings(client):
    """"R. C. Bray" and "R.C. Bray" are one group, named as most credited."""
    db.replace_owned([
        {"asin": f"O{i}", "title": f"Owned {i}", "authors": "A",
         "narrators": name, "release_date": "2020-01-01"}
        for i, name in enumerate(["R. C. Bray", "R.C. Bray", "R.C. Bray",
                                  "Kevin Free", "Kevin R. Free"])])
    html = client.get("/?tab=library&view=narrator").get_data(as_text=True)
    heads = re.findall(r'<h2 class="series-head">([^<]+?)\s*<span', html)
    assert [h.strip() for h in heads] == ["Kevin Free", "R.C. Bray"]
    assert "(3 books)" in html and "(2 books)" in html


# ------------------------------------------------------- pre-order filters

@pytest.fixture
def preorders(monkeypatch):
    """Three upcoming books: long fantasy from a series, a short romance
    from an author, an undated-length one from a narrator."""
    monkeypatch.setattr(mock_api, "CATALOG", [
        make_product("F1", "Long Fantasy", ["Test Author"], "2099-01-01",
                     "Great Series", "1", "SER123", genres=FANTASY,
                     minutes=900),
        make_product("F2", "Short Romance", ["Test Author"], "2099-02-01",
                     genres=ROMANCE, minutes=120),
        make_product("F3", "Read by Ray", ["Elsewhere"], "2099-03-01",
                     narrators=["Ray Porter"], minutes=None)])
    for t, n, a in (("series", "Great Series", "SER123"),
                    ("author", "Test Author", None),
                    ("narrator", "Ray Porter", None)):
        checker.baseline_watch(db.add_watch(t, n, a))


def _titles(client, query=""):
    html = client.get("/?tab=preorders" + query).get_data(as_text=True)
    return {t for t in ("Long Fantasy", "Short Romance", "Read by Ray")
            if t in html}, html


def test_filters_by_genre_length_and_source(client, preorders):
    assert _titles(client)[0] == {"Long Fantasy", "Short Romance",
                                  "Read by Ray"}
    shown, html = _titles(client, "&genre=Romance")
    assert shown == {"Short Romance"}
    assert "Showing 1 of 3" in html and "Clear filters" in html
    # a sub-genre matches books of either top-level genre
    assert _titles(client, "&genre=Romance+%E2%80%BA+Fantasy")[0] == {
        "Short Romance"}
    # unknown length never hides a book
    assert _titles(client, "&length=6")[0] == {"Long Fantasy", "Read by Ray"}
    assert _titles(client, "&source=narrator")[0] == {"Read by Ray"}


def test_genre_choices_come_from_the_books(client, preorders):
    _, html = _titles(client)
    assert '<option value="Romance"' in html
    assert "– Fantasy (1)" in html                  # sub-genre, indented


def test_unknown_filter_values_are_ignored(client, preorders):
    shown, html = _titles(client, "&genre=Nope&length=x&source=<b>")
    assert len(shown) == 3 and "Clear filters" not in html
