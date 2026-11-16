"""Regression tests for the fourth review (v1.0.3) and for replacing the
"Clean up duplicates" button with automatic placeholder cleanup."""
import os
import time

import pytest
from werkzeug.middleware.proxy_fix import ProxyFix

import account
import app as webapp
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


@pytest.fixture(autouse=True)
def no_lockouts():
    webapp._login_failures.clear()
    yield
    webapp._login_failures.clear()


def _down(*a, **k):
    raise audible_api.AudibleUnavailable("down")


SAME_NAME_OTHER_SERIES = [
    make_product(f"L{i}", f"Other Legacy {i}", ["Someone Else"],
                 f"202{i}-01-01", "Great Series", str(i), "OTHER9")
    for i in range(1, 4)]


# ---- A1. a series never turns into a different series with the same name ---

def test_series_added_during_outage_keeps_its_series(client, monkeypatch):
    monkeypatch.setattr(mock_api, "CATALOG",
                        mock_api.CATALOG + SAME_NAME_OTHER_SERIES)
    real = audible_api.fetch_watch_books
    monkeypatch.setattr(audible_api, "fetch_watch_books", _down)
    client.post("/watch/add", data={"type": "series", "name": "Great Series",
                                    "asin": "SER123"})
    monkeypatch.setattr(audible_api, "fetch_watch_books", real)
    checker.check_all()
    w = next(w for w in db.list_watches() if w["type"] == "series")
    assert w["asin"] == "SER123"
    assert {b["asin"] for b in db.books_for_watch(w["id"])} == {"B001", "B002"}
    authors = {w["name"] for w in db.list_watches() if w["type"] == "author"}
    assert authors == {"Test Author"}          # not "Someone Else"


def test_marketplace_change_picks_the_series_by_its_authors(client, watched,
                                                            monkeypatch):
    client.post("/settings", data={"check_hour": "8", "marketplace": "uk"})
    uk = [dict(p, asin="UK" + p["asin"],
               series=[dict(s, asin="UKSER") for s in p.get("series", [])])
          for p in mock_api.CATALOG]
    monkeypatch.setattr(mock_api, "CATALOG", uk + SAME_NAME_OTHER_SERIES)
    checker.check_all()
    assert db.get_watch(watched["series"])["asin"] == "UKSER"


def test_ambiguous_name_with_nothing_to_compare_is_not_guessed(monkeypatch):
    monkeypatch.setattr(mock_api, "CATALOG",
                        mock_api.CATALOG + SAME_NAME_OTHER_SERIES)
    wid = db.add_watch("series", "Great Series", "STALE1")  # dead ID, no books
    checker.check_all()
    w = db.get_watch(wid)
    assert w["asin"] == "STALE1"
    assert "no books for this series" in (w["last_error"] or "")


# ---- A2. pre-orders survive an orders-feed hiccup --------------------------

def test_preorders_kept_when_the_order_list_fails(monkeypatch, tmp_path):
    class Auth:
        def to_file(self, path, **k):
            open(path, "w").write("{}")

    class Client:
        fail = False

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, path, **p):
            if path == "1.0/library":
                return {"items": [make_product("L1", "Owned", ["W"],
                                               "2020-01-01")]}
            if Client.fail:
                raise RuntimeError("orders feed HTTP 500")
            return {"orders": [{"detail_state": "ORDER_CREATED",
                                "items": [{"asin": "B004"}]}]}
    mock_api.state["include_new"] = True       # B004 is a 2099 pre-order
    monkeypatch.setattr(account.audible.Authenticator, "from_file",
                        staticmethod(lambda p: Auth()))
    monkeypatch.setattr(account.audible, "Client", lambda auth: Client())
    account.sync_library()
    assert [b["asin"] for b in db.owned_preorders()] == ["B004"]
    Client.fail = True
    account.sync_library()
    assert [b["asin"] for b in db.owned_preorders()] == ["B004"]
    titles = {b["title"] for b in db.owned_books_list()}
    assert "The Third Book (NEW)" in titles and "Owned" in titles


# ---- A3. sign-in lockout and reverse proxies --------------------------------

def _login(client, password, ip="10.9.9.1", forwarded=None):
    headers = {"X-Forwarded-For": forwarded} if forwarded else {}
    return client.post("/login", data={"password": password},
                       environ_base={"REMOTE_ADDR": ip}, headers=headers)


def test_behind_a_proxy_each_visitor_is_counted_separately(client,
                                                           monkeypatch):
    monkeypatch.setenv("AW_PASSWORD", "s3cret")
    monkeypatch.setattr(webapp.app, "wsgi_app",
                        ProxyFix(webapp.app.wsgi_app, x_for=1))
    for _ in range(webapp.FREE_ATTEMPTS + 1):
        _login(client, "nope", ip="172.17.0.1", forwarded="203.0.113.9")
    assert _login(client, "s3cret", ip="172.17.0.1",
                  forwarded="203.0.113.9").status_code == 429
    assert _login(client, "s3cret", ip="172.17.0.1",
                  forwarded="192.168.1.50").status_code == 302


def test_without_proxy_setting_forwarded_header_is_ignored(client,
                                                           monkeypatch):
    """A guesser can't dodge the lockout by inventing X-Forwarded-For."""
    monkeypatch.setenv("AW_PASSWORD", "s3cret")
    for i in range(webapp.FREE_ATTEMPTS + 1):
        _login(client, "nope", ip="10.1.1.1", forwarded=f"198.51.100.{i}")
    assert _login(client, "s3cret", ip="10.1.1.1",
                  forwarded="198.51.100.99").status_code == 429


def test_proxy_count_setting(monkeypatch):
    monkeypatch.setenv("AW_PROXY_COUNT", "2")
    assert webapp._proxy_count() == 2
    monkeypatch.setenv("AW_PROXY_COUNT", "lots")
    assert webapp._proxy_count() == 0
    monkeypatch.delenv("AW_PROXY_COUNT")
    assert webapp._proxy_count() == 0


# ---- A4. the wrong-password table forgets ----------------------------------

def test_failure_table_forgets_old_entries(monkeypatch):
    clock = [time.monotonic()]
    monkeypatch.setattr(webapp.time, "monotonic", lambda: clock[0])
    for i in range(500):
        webapp._record_failure(f"ip:10.0.{i // 250}.{i % 250}")
    assert len(webapp._login_failures) == 500
    clock[0] += webapp.FORGET_AFTER + 1
    webapp._record_failure("ip:10.9.9.9")
    assert len(webapp._login_failures) == 1


def test_failure_table_has_a_size_limit(monkeypatch):
    monkeypatch.setattr(webapp, "MAX_TRACKED", 50)
    for i in range(80):
        webapp._record_failure(f"ip:10.0.0.{i}")
    assert len(webapp._login_failures) == 50
    assert "ip:10.0.0.79" in webapp._login_failures   # newest kept


# ---- A5. accented names ------------------------------------------------------

def test_accented_names_match_ignoring_case(client):
    db.add_watch("author", "Åsa Larsson", None)
    assert db.find_watch("author", "åsa larsson") is not None
    resp = client.post("/watch/add", data={"type": "author",
                                           "name": "ÅSA LARSSON"})
    assert len(db.list_watches()) == 1
    assert "/watch/" in resp.location


# ---- A6. "the store search couldn't say" keeps a book as it is -----------------

def test_no_answer_keeps_hidden_books_hidden_and_asks_again(watched,
                                                            monkeypatch):
    answers = {"B002": False}
    monkeypatch.setattr(
        audible_api, "check_market_availability",
        lambda asin, title, author=None, marketplace="us":
        answers.get(asin, True))
    checker.verify_market_availability()          # B002 judged not sold
    answers["B002"] = None                        # search returns nothing
    with db.conn() as c:
        c.execute("UPDATE books SET available_checked_at = "
                  "'2020-01-01T00:00:00Z' WHERE asin = 'B002'")
    assert checker.recheck_not_sold_here("us") == 0
    assert "B002" not in {b["asin"] for b in
                          db.books_for_watch(watched["author"])}
    assert db.books_to_recheck() == []            # asked; again next week


# ---- A7. README names the setting --------------------------------------------

def test_readme_names_the_publish_date_setting():
    readme = open(os.path.join(os.path.dirname(webapp.__file__), "README.md"),
                  encoding="utf-8").read()
    assert ('Settings →\n  Monitoring → untick "Show when books first came '
            'out in print"') in readme
    html_label = "Show when books first came out in print"
    settings = open(os.path.join(os.path.dirname(webapp.__file__),
                                 "templates", "settings.html"),
                    encoding="utf-8").read()
    assert html_label in settings and "id=\"monitoring\"" in settings


# ---- B. automatic placeholder cleanup ----------------------------------------

DEFIANT = [  # Skyward #4 as it looks on Audible: two narrations and a stub
    make_product("DEF-SJ", "Defiant", ["Test Author"], "2023-11-21",
                 "Great Series", "4", "SER123"),
    make_product("DEF-SA", "Defiant", ["Test Author"], "2023-11-21",
                 "Great Series", "4", "SER123"),
    make_product("DEF-TBA", "Defiant", ["Test Author"], "2200-01-01",
                 "Great Series", "4", "SER123"),   # Audible's TBA date
]


def test_no_date_stub_is_hidden_and_never_emailed(watched, outbox,
                                                  monkeypatch):
    checker.check_all()                           # watches on current fetch
    monkeypatch.setattr(mock_api, "CATALOG", mock_api.CATALOG + DEFIANT)
    checker.check_all()
    sid = watched["series"]
    visible = {b["asin"] for b in db.books_for_watch(sid)}
    assert {"DEF-SJ", "DEF-SA"} <= visible        # both narrations stay
    assert "DEF-TBA" not in visible
    hidden = {b["asin"]: b for b in db.books_for_watch(sid, include_hidden=True)
              if b["hidden"]}
    assert hidden["DEF-TBA"]["placeholder_hidden"]
    emailed = {b.get("asin") for batch in outbox for b in batch}
    assert "DEF-TBA" not in emailed
    assert "DEF-TBA" not in {b["asin"] for b in db.new_books()}


def test_unhidden_stub_stays_visible(watched, monkeypatch):
    monkeypatch.setattr(mock_api, "CATALOG", mock_api.CATALOG + DEFIANT)
    checker.check_all()
    sid = watched["series"]
    db.set_book_hidden("DEF-TBA", sid, False)     # you want it back
    checker.check_all()
    checker.check_all()
    assert "DEF-TBA" in {b["asin"] for b in db.books_for_watch(sid)}


def test_alternate_versions_are_never_hidden(watched, monkeypatch):
    versions = [
        make_product("LOCK-WW", "Lock In (Narrated by Wil Wheaton)",
                     ["Test Author"], "2014-08-26", "Great Series", "5",
                     "SER123"),
        make_product("LOCK-AB", "Lock In (Narrated by Amber Benson)",
                     ["Test Author"], "2014-08-26", "Great Series", "5",
                     "SER123"),
        make_product("NET", "Network Effect", ["Test Author"], "2020-05-05",
                     "Great Series", "6", "SER123"),
        make_product("NET-DRAMA", "Network Effect [Dramatized Adaptation]",
                     ["Test Author"], "2025-01-07", "Great Series", "6",
                     "SER123"),
        make_product("NET-RERELEASE", "Network Effect", ["Test Author"],
                     "2026-02-01", "Great Series", "6", "SER123"),
        # a dramatized version announced without a date, next to the
        # regular dated book: a different product, not a placeholder
        make_product("SYS", "System Collapse", ["Test Author"], "2023-11-14",
                     "Great Series", "7", "SER123"),
        make_product("SYS-DRAMA", "System Collapse (Dramatized Adaptation)",
                     ["Test Author"], "2200-01-01", "Great Series", "7",
                     "SER123"),
    ]
    monkeypatch.setattr(mock_api, "CATALOG", mock_api.CATALOG + versions)
    checker.check_all()
    visible = {b["asin"] for b in db.books_for_watch(watched["series"])}
    assert {"LOCK-WW", "LOCK-AB", "NET", "NET-DRAMA", "NET-RERELEASE",
            "SYS", "SYS-DRAMA"} <= visible


def test_clean_up_duplicates_button_is_gone(client, watched):
    html = client.get(f"/watch/{watched['author']}").get_data(as_text=True)
    assert "Clean up duplicates" not in html
    resp = client.post(f"/watch/{watched['author']}/hide-duplicates")
    assert resp.status_code in (404, 405)
    assert not hasattr(db, "hide_duplicate_books")


def test_watch_page_says_why_a_stub_is_hidden(client, watched, monkeypatch):
    monkeypatch.setattr(mock_api, "CATALOG", mock_api.CATALOG + DEFIANT)
    checker.check_all()
    html = client.get(f"/watch/{watched['series']}").get_data(as_text=True)
    assert "placeholder without a release date" in html


def test_region_check_respects_a_manual_unhide(watched, monkeypatch):
    aid = watched["author"]
    db.set_book_hidden("B002", aid, True)
    db.set_book_hidden("B002", aid, False)        # you chose to keep it
    db.hide_asin_everywhere("B002")               # later judged "not sold"
    assert "B002" in {b["asin"] for b in db.books_for_watch(aid)}
