"""Web routes: watch management, page rendering, library and owned badges."""
import pytest
import checker
import db
import mock_api


def test_series_add_auto_adds_author(client):
    resp = client.post("/watch/add", data={"type": "series",
                                           "name": "Great Series",
                                           "asin": "SER123"})
    assert resp.status_code == 302
    watches = {(w["type"], w["name"]) for w in db.list_watches()}
    assert ("series", "Great Series") in watches
    assert ("author", "Test Author") in watches
    # re-adding must not duplicate anything
    n = len(db.list_watches())
    client.post("/watch/add", data={"type": "series", "name": "Great Series",
                                    "asin": "SER123"})
    assert len(db.list_watches()) == n


def test_add_watch_fetch_gets_json(client):
    resp = client.post("/watch/add",
                       data={"type": "series", "name": "Great Series",
                             "asin": "SER123"},
                       headers={"X-Requested-With": "fetch"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] and "/watch/" in data["watch_url"]
    resp = client.post("/watch/add", data={"type": "author", "name": ""},
                       headers={"X-Requested-With": "fetch"})
    assert resp.status_code == 400
    assert not resp.get_json()["ok"]


def test_remove_watch_cascades(watched):
    db.remove_watch(watched["series"])
    assert all(w["id"] != watched["series"] for w in db.list_watches())
    assert db.books_for_watch(watched["series"]) == []


def test_all_pages_render(client, watched):
    pages = ["/", "/?tab=authors", "/?tab=series", "/?tab=preorders",
             "/?tab=library", "/?tab=library&view=author",
             "/?tab=library&view=series",
             "/search?q=Test+Author", "/search?q=Great+Series",
             "/settings"]
    for w in db.list_watches():
        pages += [f"/watch/{w['id']}", f"/watch/{w['id']}?view=series"]
    for p in pages:
        assert client.get(p).status_code == 200, p


OWNED = [
    {"asin": "B001", "title": "The First Book", "authors": "Test Author",
     "series_title": "Great Series", "series_sequence": "1",
     "series_asin": "SER123", "release_date": "2024-03-01",
     "url": "https://example.com/B001", "purchase_date": "2024-04-01"},
    {"asin": "B100", "title": "Unrelated Novel", "authors": "Solo Writer",
     "series_title": None, "release_date": "2023-01-01",
     "url": "https://example.com/B100", "purchase_date": "2023-02-01"},
]


def test_owned_badges_on_watch_page(client, watched):
    db.replace_owned(OWNED)
    assert db.owned_asins() == {"B001", "B100"}
    resp = client.get(f"/watch/{watched['author']}")
    assert b"badge-owned" in resp.data
    assert b"you own 1 of" in resp.data
    db.replace_owned([])
    resp = client.get(f"/watch/{watched['author']}")
    assert b"badge-owned" not in resp.data


def test_library_tab(client, watched):
    db.replace_owned(OWNED)
    resp = client.get("/?tab=library")
    assert b"The First Book" in resp.data
    assert b"purchased: 2024-04-01" in resp.data
    assert b"Hide" not in resp.data, "owned cards have no watch actions"
    db.replace_owned([])
    resp = client.get("/?tab=library")
    assert b"No owned books synced yet" in resp.data


def test_library_group_views(client, watched):
    db.replace_owned(OWNED)
    resp = client.get("/?tab=library&view=author")
    assert b'<details class="group"' in resp.data
    assert "Watching ✓".encode() in resp.data       # Test Author is watched
    assert b"+ Watch author" in resp.data           # Solo Writer is not
    resp = client.get("/?tab=library&view=series")
    assert b"Great Series" in resp.data
    assert "Watching ✓".encode() in resp.data       # Great Series is watched
    assert b"+ Watch series" not in resp.data       # Standalone: no button


SORT_OWNED = [
    # Alpha Saga: 1 book, most recent purchase; Beta Saga: 2 books, older
    {"asin": "S1", "title": "Alpha One", "authors": "A",
     "series_title": "Alpha Saga", "series_sequence": "1",
     "release_date": "2026-01-01", "purchase_date": "2026-06-01",
     "url": "u"},
    {"asin": "S2", "title": "Beta One", "authors": "B",
     "series_title": "Beta Saga", "series_sequence": "1",
     "release_date": "2020-01-01", "purchase_date": "2020-02-01",
     "url": "u"},
    {"asin": "S3", "title": "Beta Two", "authors": "B",
     "series_title": "Beta Saga", "series_sequence": "2",
     "release_date": "2021-01-01", "purchase_date": "2021-02-01",
     "url": "u"},
]


def _series_order(html):
    names = []
    for line in html.decode().splitlines():
        if "Alpha Saga" in line and "series-head" in line:
            names.append("Alpha Saga")
        if "Beta Saga" in line and "series-head" in line:
            names.append("Beta Saga")
    return names


def test_library_series_sorts(client):
    db.replace_owned(SORT_OWNED)
    # recent purchase (default): Alpha (2026 purchase) before Beta
    resp = client.get("/?tab=library&view=series")
    assert _series_order(resp.data) == ["Alpha Saga", "Beta Saga"]
    # newest book: Alpha (2026 release) first
    resp = client.get("/?tab=library&view=series&sort=newest")
    assert _series_order(resp.data) == ["Alpha Saga", "Beta Saga"]
    # most owned: Beta (2 books) first
    resp = client.get("/?tab=library&view=series&sort=owned")
    assert _series_order(resp.data) == ["Beta Saga", "Alpha Saga"]
    # biggest series: Beta first (2 known books vs 1)
    resp = client.get("/?tab=library&view=series&sort=largest")
    assert _series_order(resp.data) == ["Beta Saga", "Alpha Saga"]


def test_preordered_tag(client, watched):
    mock_api.state["include_new"] = True
    checker.check_all()  # B004 (releases 2099-10-20) enters the watches
    db.replace_owned([{"asin": "B004", "title": "The Third Book (NEW)",
                       "authors": "Test Author", "preordered": 1,
                       "release_date": "2099-10-20",
                       "url": "https://example.com/B004"}])
    resp = client.get(f"/watch/{watched['author']}")
    assert "PRE-ORDERED ✓".encode() in resp.data
    resp = client.get("/?tab=preorders")
    assert "PRE-ORDERED ✓".encode() in resp.data
    assert b"following" in resp.data  # split into series/authors sections


def test_library_lists_preorders_first(client):
    db.replace_owned([
        {"asin": "OLD", "title": "Bought Long Ago", "authors": "A",
         "purchase_date": "2015-01-01", "release_date": "2014-01-01",
         "url": "u"},
        {"asin": "LATER", "title": "Pre-order Later", "authors": "A",
         "preordered": 1, "release_date": "2027-06-15", "url": "u"},
        {"asin": "RECENT", "title": "Bought Last Week", "authors": "A",
         "purchase_date": "2026-11-08", "release_date": "2026-11-01",
         "url": "u"},
        {"asin": "SOON", "title": "Pre-order Soon", "authors": "A",
         "preordered": 1, "release_date": "2026-12-08", "url": "u"},
    ])
    assert [b["asin"] for b in db.owned_books_list()] == \
        ["SOON", "LATER", "RECENT", "OLD"]
    html = client.get("/?tab=library").get_data(as_text=True)
    order = [html.index(t) for t in ("Pre-order Soon", "Pre-order Later",
                                     "Bought Last Week", "Bought Long Ago")]
    assert order == sorted(order)


def test_released_preorders_are_not_pinned(client):
    db.replace_owned([
        {"asin": "OUT", "title": "Came Out Last Month", "authors": "A",
         "preordered": 1, "release_date": "2020-01-01", "url": "u"},
        {"asin": "BOUGHT", "title": "Bought", "authors": "A",
         "purchase_date": "2026-01-01", "release_date": "2025-01-01",
         "url": "u"},
        {"asin": "SOON", "title": "Still Coming", "authors": "A",
         "preordered": 1, "release_date": "2099-01-01", "url": "u"},
    ])
    assert [b["asin"] for b in db.owned_books_list()] == \
        ["SOON", "BOUGHT", "OUT"]


SORTABLE = [
    {"asin": "S1", "title": "The Zebra Story", "authors": "Ann Young",
     "purchase_date": "2026-01-01", "release_date": "2010-01-01",
     "runtime_minutes": 300, "url": "u"},
    {"asin": "S2", "title": "Apples", "authors": "Lee Child",
     "purchase_date": "2024-01-01", "release_date": "2025-06-01",
     "runtime_minutes": 725, "url": "u"},
    {"asin": "S3", "title": "A Middle Book", "authors": "Martin Luther King, Jr.",
     "purchase_date": "2025-01-01", "release_date": "2001-01-01",
     "runtime_minutes": 45, "url": "u"},
    {"asin": "S4", "title": "Coming Soon", "authors": "Bea Adams",
     "preordered": 1, "release_date": "2099-01-01", "url": "u"},
]


def _all_order(client, sort):
    html = client.get(f"/?tab=library&sort={sort}").get_data(as_text=True)
    titles = [b["title"] for b in SORTABLE]
    return sorted(titles, key=lambda t: html.index(f">{t}<"))


@pytest.mark.parametrize("sort, expected", [
    ("recent", ["Coming Soon", "The Zebra Story", "A Middle Book", "Apples"]),
    ("release", ["Coming Soon", "Apples", "The Zebra Story", "A Middle Book"]),
    ("title", ["Apples", "Coming Soon", "A Middle Book", "The Zebra Story"]),
    ("author", ["Coming Soon", "Apples", "A Middle Book", "The Zebra Story"]),
    ("length", ["Apples", "The Zebra Story", "A Middle Book", "Coming Soon"]),
])
def test_all_books_sorts(client, sort, expected):
    db.replace_owned(SORTABLE)
    assert _all_order(client, sort) == expected


def test_all_books_sort_buttons_and_lengths(client):
    db.replace_owned(SORTABLE)
    html = client.get("/?tab=library").get_data(as_text=True)
    for label in ("Recent purchase", "Release date", "Title", "Author",
                  "Length"):
        assert f">{label}</a>" in html
    assert "12h 05m" not in html               # lengths only when sorting by them
    html = client.get("/?tab=library&sort=length").get_data(as_text=True)
    assert "12h 05m" in html and "45m" in html
    # an unknown sort falls back to the default instead of failing
    assert client.get("/?tab=library&sort=bogus").status_code == 200


def test_series_names_link_to_the_series(client, watched):
    import re
    html = client.get(f"/watch/{watched['author']}").get_data(as_text=True)
    sid = watched["series"]
    assert f'class="series-link" href="/watch/{sid}">Great Series</a>' in html
    # a series you don't watch links to the in-app search for it
    db.remove_watch(sid)
    html = client.get(f"/watch/{watched['author']}").get_data(as_text=True)
    assert re.search(r'class="series-link" href="/search\?q=Great\+Series'
                     r'(&amp;|&)type=series">Great Series</a>', html)


def test_series_page_links_to_audible(client, watched):
    html = client.get(f"/watch/{watched['series']}").get_data(as_text=True)
    assert 'href="https://www.audible.com/series/SER123"' in html
    assert "View on Audible" in html
    html = client.get(f"/watch/{watched['author']}").get_data(as_text=True)
    assert "View on Audible" not in html     # author pages: no series link
    db.save_settings({"marketplace": "uk"})
    html = client.get(f"/watch/{watched['series']}").get_data(as_text=True)
    assert 'href="https://www.audible.co.uk/series/SER123"' in html


def test_series_page_without_id_links_to_audible_search(client):
    wid = db.add_watch("series", "Mystery Saga", None)
    html = client.get(f"/watch/{wid}").get_data(as_text=True)
    assert "https://www.audible.com/search?keywords=Mystery+Saga" in html


def test_series_search_results_link_to_audible(client):
    html = client.get("/search?q=Great+Series").get_data(as_text=True)
    assert 'href="https://www.audible.com/series/SER123"' in html


def test_account_lives_on_the_settings_page(client):
    html = client.get("/settings").get_data(as_text=True)
    assert 'id="account"' in html and "Connect Audible account" in html
    nav = html[html.index("<nav>"):html.index("</nav>")]
    assert "Account" not in nav
    # old links and the account actions land on the section
    resp = client.get("/account")
    assert resp.status_code == 302
    assert resp.location.endswith("/settings#account")
    resp = client.post("/account/complete", data={"response_url": ""})
    assert resp.location.endswith("/settings#account")


def test_account_section_is_first_on_settings(client):
    html = client.get("/settings").get_data(as_text=True)
    assert html.index('id="account"') < html.index('class="settings-form"')
