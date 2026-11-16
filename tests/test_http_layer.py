"""The real Audible HTTP layer, below the catalog mock: retries, response
parsing, author matching, series lookup and the marketplace check. Requests
never leave the machine: requests.get is replaced by a scripted fake."""
import pytest
import requests

import audible_api
import bookinfo
import mock_api
from mock_api import make_product


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None, bad_json=False):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


@pytest.fixture
def http(monkeypatch):
    """Script requests.get: append responses (or exceptions) to `queue`, or
    set `handler(url, params)`. Every call lands in `calls`; sleeps are
    recorded, not slept."""
    state = {"queue": [], "calls": [], "sleeps": [], "handler": None}

    def fake_get(url, params=None, headers=None, timeout=None):
        state["calls"].append((url, dict(params or {})))
        if state["handler"]:
            return state["handler"](url, params or {})
        item = state["queue"].pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(audible_api.requests, "get", fake_get)
    monkeypatch.setattr(audible_api.time, "sleep", state["sleeps"].append)
    # un-mock the layer under test
    monkeypatch.setattr(audible_api, "_get", mock_api.REAL["_get"])
    monkeypatch.setattr(audible_api, "_series_child_asins",
                        mock_api.REAL["_series_child_asins"])
    return state


# ---- retries ---------------------------------------------------------------

def test_success_needs_no_retry(http):
    http["queue"] = [FakeResponse(payload={"products": []})]
    assert audible_api._get_json("u", {}) == {"products": []}
    assert http["sleeps"] == []


def test_network_error_and_5xx_are_retried(http):
    http["queue"] = [requests.ConnectionError("down"), FakeResponse(503),
                     FakeResponse(payload={"ok": 1})]
    assert audible_api._get_json("u", {}) == {"ok": 1}
    assert http["sleeps"] == [2, 4]


def test_rate_limit_honours_retry_after_capped(http):
    http["queue"] = [FakeResponse(429, headers={"Retry-After": "7"}),
                     FakeResponse(429, headers={"Retry-After": "9999"}),
                     FakeResponse(payload={"ok": 1})]
    assert audible_api._get_json("u", {}) == {"ok": 1}
    assert http["sleeps"] == [7, audible_api.MAX_RETRY_AFTER]


def test_client_errors_fail_fast(http):
    http["queue"] = [FakeResponse(404)]
    with pytest.raises(audible_api.AudibleAPIError, match="HTTP 404"):
        audible_api._get_json("u", {})
    assert len(http["calls"]) == 1 and http["sleeps"] == []


def test_no_sleep_after_the_last_attempt(http):
    http["queue"] = [FakeResponse(500)] * 3
    with pytest.raises(audible_api.AudibleAPIError, match="HTTP 500"):
        audible_api._get_json("u", {})
    assert http["sleeps"] == [2, 4]


def test_invalid_json_is_an_api_error(http):
    http["queue"] = [FakeResponse(bad_json=True)]
    with pytest.raises(audible_api.AudibleAPIError, match="isn't JSON"):
        audible_api._get_json("u", {})


# ---- parsing ---------------------------------------------------------------

def test_simplify_tba_placeholder_and_fields():
    p = make_product("B1", "Title", ["A One", "B Two"], "2200-01-01",
                     "Saga", "2", "S1")
    b = audible_api._simplify(p, "uk")
    assert b["release_date"] is None  # Audible's far-future TBA date
    assert b["authors"] == "A One, B Two"
    assert b["author_list"] == ["A One", "B Two"]
    assert (b["series_title"], b["series_sequence"]) == ("Saga", "2")
    assert b["url"] == "https://www.audible.co.uk/pd/B1"


# ---- author search ---------------------------------------------------------

def _products(*products):
    return FakeResponse(payload={"products": list(products)})


def test_author_search_pages_until_short_page(http):
    page = [make_product(f"P{i}", f"T{i}", ["Ann Writer"], "2024-01-01")
            for i in range(50)]
    http["queue"] = [_products(*page), _products(page[0])]
    books = audible_api.search_by_author("Ann Writer", max_results=200)
    assert len(books) == 51
    assert [c[1]["page"] for c in http["calls"]] == [0, 1]


def test_watch_author_match_is_strict(http):
    http["queue"] = [_products(
        make_product("A", "Mine", ["J. R. R. Tolkien"], "2024-01-01"),
        make_product("B", "Not mine", ["Christopher Tolkien"], "2024-01-01"),
        make_product("C", "Co-written", ["Someone, Jr.", "J.R.R. Tolkien"],
                     "2024-01-01"),
        make_product("D", "Lumped credit", ["J.R.R. Tolkien & C. Tolkien"],
                     "2024-01-01"),
        make_product("E", "Foreword only", ["Ann Writer",
                                            "J.R.R. Tolkien - foreword"],
                     "2024-01-01"),
        make_product("F", "Sound-alike", ["J.R.R. Tolkiens"], "2024-01-01"))]
    books = audible_api.fetch_watch_books("author", "J.R.R. Tolkien", None)
    assert {b["asin"] for b in books} == {"A", "C", "D"}


def test_watch_author_with_no_exact_match_gets_nothing(http):
    http["queue"] = [_products(
        make_product("X", "Other", ["Ann Writerson"], "2024-01-01"))]
    assert audible_api.fetch_watch_books("author", "Ann Writer", None) == []


def test_search_still_offers_near_matches(http):
    http["queue"] = [_products(
        make_product("X", "Other", ["Ann Writerson"], "2024-01-01"))]
    assert len(audible_api.search_by_author("Anne Writer")) == 1


# ---- series ----------------------------------------------------------------

def test_series_uses_relationships_without_a_cap(http):
    children = [{"asin": f"S{i:03}", "relationship_to_product": "child",
                 "relationship_type": "series"} for i in range(120)]
    children.append({"asin": "PARENT", "relationship_to_product": "parent",
                     "relationship_type": "series"})

    def handler(url, params):
        if url.endswith("/SERIES1"):
            return FakeResponse(payload={"product": {"relationships":
                                                     children}})
        asins = params["asins"].split(",")
        return _products(*[make_product(a, a, ["W"], "2024-01-01",
                                        "Long", "1", "SERIES1")
                           for a in asins])
    http["handler"] = handler
    books = audible_api.books_in_series("Long", "SERIES1")
    assert len(books) == 120  # was capped at 100
    assert "PARENT" not in {b["asin"] for b in books}


def test_series_outage_does_not_fall_back(http):
    """When Audible is down, a fallback keyword search would only time out
    too: give up at once so the check can count the outage."""
    http["handler"] = lambda url, params: FakeResponse(503)
    with pytest.raises(audible_api.AudibleUnavailable):
        audible_api.books_in_series("Saga", "SER9")
    assert all(u.endswith("/SER9") for u, _ in http["calls"])


def test_connect_timeout_is_short(http):
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen["timeout"] = timeout
        return FakeResponse(payload={})
    audible_api.requests.get = fake_get  # restored by monkeypatch
    audible_api._get_json("u", {})
    assert seen["timeout"] == (10, 30)


def test_series_falls_back_to_keyword_search(http):
    def handler(url, params):
        if url.endswith("/SER9"):
            return FakeResponse(404)
        return _products(
            make_product("K1", "Book", ["W"], "2024-01-01", "Saga", "1",
                         "SER9"),
            make_product("K2", "Other", ["W"], "2024-01-01", "Other", "1",
                         "SER8"))
    http["handler"] = handler
    books = audible_api.books_in_series("Saga", "SER9")
    assert [b["asin"] for b in books] == ["K1"]


# ---- marketplace availability ---------------------------------------------

def test_market_availability(http):
    http["queue"] = [_products(make_product("A1", "T", ["W"], "2024-01-01"))]
    assert audible_api.check_market_availability("A1", "T", "W") is True
    http["queue"] = [_products(make_product("A2", "T", ["W"], "2024-01-01"))]
    assert audible_api.check_market_availability("A1", "T", "W") is False
    # empty with the author, empty title-only: no conclusion
    http["queue"] = [_products(), _products()]
    assert audible_api.check_market_availability("A1", "T", "W") is None
    assert "author" not in http["calls"][-1][1]


# ---- publish-date title matching ------------------------------------------

@pytest.mark.parametrize("a, b, same", [
    ("the way of kings", "the way of kings the stormlight archive book 1",
     True),
    ("the way of kings", "the way of kings", True),
    ("it", "it ends with us", False),
    ("it", "itch", False),
    ("it", "it", True),
    ("dune", "dune messiah", False),
    ("the hobbit", "the hobbitses", False),
    ("", "anything", False),
])
def test_titles_match(a, b, same):
    assert bookinfo._titles_match(a, b) is same
