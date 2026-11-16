"""Catalog client behavior against the mocked Audible API."""
import audible_api


def test_author_search_newest_first():
    books = audible_api.search_by_author("Test Author")
    assert len(books) == 3
    assert books[0]["asin"] == "B001"


def test_series_candidates_rank_exact_match_first():
    cands = audible_api.search_series_candidates("Great Series")
    assert cands
    assert cands[0]["series_title"] == "Great Series"
    assert cands[0]["series_asin"] == "SER123"


def test_matches_language():
    assert audible_api.matches_language({"language": "english"}, "english")
    assert not audible_api.matches_language({"language": "german"}, "english")
    # unknown language gets the benefit of the doubt
    assert audible_api.matches_language({"language": None}, "english")
    # no preference set keeps everything
    assert audible_api.matches_language({"language": "german"}, "")
