"""Regression tests for the second review (2026-11-15), items 1-7."""
import pytest

import app as webapp
import audible_api
import checker
import db
import mock_api


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(checker, "send_new_release_email",
                        lambda books, settings: sent.append(books))
    db.save_settings({"email_enabled": "1"})
    return sent


# ---- 1. a failed first fetch must not flood NEW + email -------------------

def test_failed_first_fetch_is_baselined_quietly(client, outbox, monkeypatch):
    real = audible_api.fetch_watch_books

    def down(*a, **k):
        raise audible_api.AudibleUnavailable("HTTP 503")
    monkeypatch.setattr(audible_api, "fetch_watch_books", down)
    client.post("/watch/add", data={"type": "author", "name": "Test Author"})
    monkeypatch.setattr(audible_api, "fetch_watch_books", real)
    w = db.list_watches()[0]
    assert w["baselined_at"] is None

    result = checker.check_all()  # first successful fetch
    assert result["new_books"] == 0
    assert db.new_books() == [] and outbox == []
    assert db.list_watches()[0]["baselined_at"]
    assert db.list_watches()[0]["book_count"] == 3

    mock_api.state["include_new"] = True  # later releases still alert
    assert checker.check_all()["new_books"] == 1
    assert [b["asin"] for b in outbox[0]] == ["B004"]


def test_existing_watches_count_as_baselined_after_upgrade(watched):
    with db.conn() as c:  # simulate a database from before the column
        c.execute("ALTER TABLE watches DROP COLUMN baselined_at")
    db.init_db()
    assert all(w["baselined_at"] for w in db.list_watches())


# ---- 2. changing marketplace must not flag everything ----------------------

def test_marketplace_change_rebaselines(client, watched, outbox, monkeypatch):
    db.set_availability("B002", False)          # hidden as "not sold in US"
    db.hide_asin_everywhere("B002")
    resp = client.post("/settings", data={"check_hour": "8",
                                          "marketplace": "uk",
                                          "email_enabled": "on"},
                       follow_redirects=True)
    assert "re-records every watch" in resp.get_data(as_text=True)
    assert all(w["baselined_at"] is None for w in db.list_watches())
    # the region verdicts start over and the edition comes back (not new)
    assert "B002" in {b["asin"] for b in db.books_for_watch(watched["author"])}
    assert db.books_missing_availability()

    # the new store lists the same books under other ASINs
    uk = [dict(p, asin="UK" + p["asin"]) for p in mock_api.CATALOG]
    monkeypatch.setattr(mock_api, "CATALOG", uk)
    result = checker.check_all()
    assert result["new_books"] == 0 and outbox == [] and db.new_books() == []


def test_same_marketplace_save_keeps_baselines(client, watched):
    client.post("/settings", data={"check_hour": "8", "marketplace": "us"})
    assert all(w["baselined_at"] for w in db.list_watches())


# ---- 3. PRE-ORDERED only for what you actually ordered --------------------

def _card(html, text):
    """The book card containing `text` (a title or release date)."""
    i = html.index(text)
    start = html.rfind('<div class="book-card', 0, i)
    end = html.find('<div class="book-card', i)
    return html[start:end if end != -1 else len(html)]


def test_preordered_needs_the_exact_edition(client, watched):
    aid = watched["author"]
    db.upsert_books(aid, [
        {"asin": "NEWED", "title": "The First Book", "authors": "Test Author",
         "release_date": "2099-01-01", "url": "u"},
        {"asin": "MINE", "title": "Ordered Book", "authors": "Test Author",
         "release_date": "2099-02-01", "url": "u"}], as_new=False)
    db.replace_owned([
        {"asin": "UK-B001", "title": "The First Book",
         "authors": "Test Author", "url": "u"},
        {"asin": "MINE", "title": "Ordered Book", "authors": "Test Author",
         "preordered": 1, "url": "u"}])
    html = client.get(f"/watch/{aid}").get_data(as_text=True)
    new_edition = _card(html, "2099-01-01")
    assert "PRE-ORDERED" not in new_edition
    assert "badge-preorder" in new_edition
    assert "PRE-ORDERED" in _card(html, "Ordered Book")
    # the released book still counts as owned via the other edition
    assert "badge-owned" in _card(html, "2024-03-01")


# ---- 4. author watches named with '&' --------------------------------------

def test_lumped_credit_matches_its_own_watch():
    names = audible_api._credited_names(["Brandon Sanderson & Dan Wells"])
    assert audible_api.norm_name("Brandon Sanderson & Dan Wells") in names
    assert audible_api.norm_name("Brandon Sanderson") in names


# ---- 5. publish-date lookups can be switched off ---------------------------

def test_publish_date_lookups_toggle(client, monkeypatch):
    calls = []
    monkeypatch.setattr(checker, "verify_market_availability",
                        lambda: calls.append("availability"))
    monkeypatch.setattr(checker, "enrich_published_dates",
                        lambda: calls.append("dates"))
    checker.run_enrichment()
    assert calls == ["availability", "dates"]  # on by default

    client.post("/settings", data={"check_hour": "8", "marketplace": "us"})
    assert db.get_settings()["lookup_publish_dates"] == "0"
    calls.clear()
    checker.run_enrichment()
    assert calls == ["availability"]


def test_readme_lists_every_outside_service():
    import os
    readme = open(os.path.join(os.path.dirname(webapp.__file__),
                               "README.md"), encoding="utf-8").read()
    for name in ("Google Books", "Open Library", "image servers",
                 "Amazon sign-in", "email server"):
        assert name in readme


# ---- 6. allowed hostnames + optional password ------------------------------

@pytest.mark.parametrize("host, ok", [
    ("localhost:5055", True), ("127.0.0.1:5055", True), ("[::1]:5055", True),
    ("192.168.1.20:5055", True), ("nas:5055", True), ("nas.local", True),
    ("box.home.arpa:5055", True), ("audible-watcher.lan", True),
    ("evil.example.com", False), ("audible.example.org:5055", False),
    ("192.168.1.20.nip.io", False), ("", False),
])
def test_host_allowed(host, ok):
    assert webapp.host_allowed(host) is ok


def test_rebinding_host_is_refused(client, monkeypatch):
    resp = client.get("/", headers={"Host": "attacker.example.com"})
    assert resp.status_code == 400
    assert "AW_ALLOWED_HOSTS" in resp.get_data(as_text=True)
    monkeypatch.setenv("AW_ALLOWED_HOSTS", "audible.example.org, other")
    ok = client.get("/", headers={"Host": "audible.example.org:5055"})
    assert ok.status_code == 200


def test_password_protects_everything(client, monkeypatch):
    monkeypatch.setenv("AW_PASSWORD", "s3cret")
    resp = client.get("/settings")
    assert resp.status_code == 302 and "/login" in resp.location
    resp = client.post("/watch/add", data={"type": "author", "name": "X"},
                       headers={"X-Requested-With": "fetch"})
    assert resp.status_code == 401
    assert db.list_watches() == []

    monkeypatch.setattr(webapp.time, "sleep", lambda s: None)
    bad = client.post("/login", data={"password": "nope"})
    assert "That password isn" in bad.get_data(as_text=True)
    assert client.get("/").status_code == 302

    good = client.post("/login", data={"password": "s3cret",
                                       "next": "/settings"})
    assert good.status_code == 302 and good.location.endswith("/settings")
    page = client.get("/").get_data(as_text=True)
    assert "Sign out" in page

    monkeypatch.setenv("AW_PASSWORD", "changed")  # signs everyone out
    assert client.get("/").status_code == 302


def test_login_never_redirects_offsite(client, monkeypatch):
    monkeypatch.setenv("AW_PASSWORD", "s3cret")
    for target in ("//evil.example", "https://evil.example", "/\\evil"):
        resp = client.post("/login", data={"password": "s3cret",
                                           "next": target})
        assert resp.location in ("/", "http://localhost/")


def test_no_password_means_no_login(client):
    assert client.get("/login").status_code == 302
    assert "Sign out" not in client.get("/").get_data(as_text=True)


# ---- 7. a long outage must not run for hours -------------------------------

def test_outage_stops_after_three_unreachable_watches(monkeypatch):
    for i in range(6):
        db.add_watch("author", f"Author {i}", None)
    tried = []

    def down(wtype, name, asin, marketplace):
        tried.append(name)
        raise audible_api.AudibleUnavailable("timed out")
    monkeypatch.setattr(audible_api, "fetch_watch_books", down)
    result = checker.check_all()
    assert len(tried) == 3
    assert result["aborted"]
    assert "skipped the other 3" in result["errors"][-1]


def test_other_errors_do_not_count_as_an_outage(monkeypatch):
    for i in range(5):
        db.add_watch("author", f"Author {i}", None)
    tried = []

    def bad(wtype, name, asin, marketplace):
        tried.append(name)
        raise audible_api.AudibleAPIError("HTTP 404")
    monkeypatch.setattr(audible_api, "fetch_watch_books", bad)
    assert not checker.check_all()["aborted"]
    assert len(tried) == 5


def test_aborted_check_is_retried_once(monkeypatch):
    monkeypatch.setattr(checker, "check_all",
                        lambda trigger: {"aborted": True})
    assert webapp._check_lock.acquire(blocking=False)
    webapp._run_check("scheduled")
    job = webapp.scheduler.get_job("retry_check")
    assert job is not None
    webapp.scheduler.remove_job("retry_check")

    assert webapp._check_lock.acquire(blocking=False)
    webapp._run_check("retry")  # a failed retry doesn't schedule another
    assert webapp.scheduler.get_job("retry_check") is None
    assert not webapp._check_lock.locked()


def test_saving_settings_keeps_a_pending_retry(client, monkeypatch):
    monkeypatch.setattr(checker, "check_all",
                        lambda trigger: {"aborted": True})
    assert webapp._check_lock.acquire(blocking=False)
    webapp._run_check("scheduled")
    client.post("/settings", data={"check_hour": "9", "marketplace": "us"})
    assert webapp.scheduler.get_job("retry_check") is not None
    webapp.scheduler.remove_job("retry_check")
