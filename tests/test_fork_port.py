"""1.2.0: pieces taken from the mylacc/audible-watcher fork, as extended
here: "Last released" on every watch tab, the favicon, and the wishlist
running with the daily check."""
from datetime import date, timedelta

import pytest

import app as webapp
import db
import wishlist

DAY = timedelta(days=1)


def _watch(wtype, name, *release_dates):
    wid = db.add_watch(wtype, name, None)
    db.upsert_books(wid, [{"asin": f"{name[:3].upper()}{wtype[0]}{i}",
                           "title": f"{name} {i}", "authors": name,
                           "narrators": name, "url": "u", "release_date": d}
                          for i, d in enumerate(release_dates)], as_new=False)
    return wid


@pytest.mark.parametrize("wtype,tab", [("author", "authors"),
                                       ("narrator", "narrators")])
def test_last_released_on_author_and_narrator_tabs(client, wtype, tab):
    today = date.today()
    _watch(wtype, "Anna Old", (today - 300 * DAY).isoformat())
    _watch(wtype, "Zed Recent", (today - 3 * DAY).isoformat(),
           (today + 90 * DAY).isoformat())          # the pre-order doesn't count
    html = client.get(f"/?tab={tab}").get_data(as_text=True)
    assert html.index(">Anna Old</a>") < html.index(">Zed Recent</a>")
    html = client.get(f"/?tab={tab}&order=released").get_data(as_text=True)
    assert html.index(">Zed Recent</a>") < html.index(">Anna Old</a>")
    assert "<th>Last released</th>" in html
    assert f'href="/?tab={tab}&amp;order=released"' in html


@pytest.mark.parametrize("path", ["/", "/settings", "/search"])
def test_favicon_on_every_page(client, path):
    html = client.get(path).get_data(as_text=True)
    assert "favicon.svg" in html and "apple-touch-icon.png" in html


def test_favicon_files_are_served(client):
    svg = client.get("/static/favicon.svg")
    assert svg.status_code == 200 and b"<script" not in svg.data
    assert client.get("/static/favicon-32.png").status_code == 200
    assert client.get("/static/apple-touch-icon.png").status_code == 200


def test_favicon_on_the_refused_address_page(raw_client):
    html = raw_client.get("/", headers={"Host": "evil.example"}).get_data(
        as_text=True)
    assert "favicon.svg" in html


def test_daily_check_reads_the_wishlist_unless_audible_is_down(monkeypatch):
    ran = []
    monkeypatch.setattr(wishlist, "check", lambda trigger: ran.append(trigger))
    monkeypatch.setattr(webapp.updates, "check", lambda force=False: None)
    for aborted in (False, True):
        monkeypatch.setattr(webapp.checker, "check_all",
                            lambda trigger, a=aborted: {"aborted": a})
        monkeypatch.setattr(webapp.scheduler, "add_job",
                            lambda *a, **k: None)
        webapp._check_lock.acquire()
        webapp._run_check("scheduled")
    assert ran == ["scheduled"]


def test_price_check_is_scheduled_twelve_hours_after(monkeypatch):
    jobs = {}
    monkeypatch.setattr(webapp.scheduler, "add_job",
                        lambda func, trigger, id, **k: jobs.update(
                            {id: str(trigger)}))
    monkeypatch.setattr(webapp.scheduler, "remove_job", lambda id: None)
    db.save_settings({"check_hour": "20"})
    webapp.reschedule()
    assert "hour='20'" in jobs["daily_check"]
    assert "hour='8'" in jobs["price_check"]
