"""Smoke tests against Audible's real public catalog (read-only, no login).

The normal tests use a fake catalog, so they can't notice when Audible's
real data or search behaviour changes. These check a few well-known authors
and series end to end. They need the network, so they only run on request:

    AW_LIVE_TESTS=1 python -m pytest live_tests

GitHub Actions runs them weekly and on demand (live-catalog workflow).
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import audible_api  # noqa: E402

pytestmark = pytest.mark.skipif(os.environ.get("AW_LIVE_TESTS") != "1",
                                reason="set AW_LIVE_TESTS=1 to run")


@pytest.mark.parametrize("series", [
    "Dungeon Crawler Carl", "Harry Potter", "The Stormlight Archive",
    "Bobiverse", "Expeditionary Force",
])
def test_series_search_offers_the_series(series):
    found = audible_api.search_series_candidates(series, "us",
                                                 language="english")
    titles = [c["series_title"].strip().lower() for c in found]
    assert series.lower() in titles, titles[:10]


@pytest.mark.parametrize("author", ["Stephen King", "Brandon Sanderson",
                                    "Andy Weir"])
def test_author_search_offers_the_author(author):
    books = audible_api.search_by_author(author, "us", max_results=50,
                                         sort=None)
    english = [b for b in books
               if audible_api.matches_language(b, "english")]
    names = {a for b in english for a in b["author_list"]}
    assert author in names


def test_series_watch_fetches_the_books():
    asin = audible_api.resolve_series_asin("Bobiverse", "us")
    assert asin
    books = audible_api.books_in_series("Bobiverse", asin, "us")
    assert len(books) >= 5
    assert all(b["url"].startswith(audible_api.store_prefix("us"))
               for b in books)


def test_series_id_is_resolved_per_store():
    us = audible_api.resolve_series_asin("Jack Reacher", "us")
    uk = audible_api.resolve_series_asin("Jack Reacher", "uk")
    assert us and uk
    assert audible_api.books_in_series("Jack Reacher", uk, "uk")


def test_author_watch_fetch_is_strict_and_newest_first():
    books = audible_api.fetch_watch_books("author", "Andy Weir", None, "us")
    assert books
    assert all("andyweir" in audible_api._credited_names(b["author_list"])
               for b in books)
    dates = [b["release_date"] for b in books if b["release_date"]]
    assert dates == sorted(dates, reverse=True)


def test_market_availability_check():
    books = audible_api.search_by_author("Andy Weir", "us", max_results=5,
                                         strict=True)
    b = next(b for b in books if b["release_date"])
    assert audible_api.check_market_availability(
        b["asin"], b["title"], "Andy Weir", "us") is True
