"""Web routes: watch management, page rendering, library and owned badges."""
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
             "/settings", "/account"]
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
