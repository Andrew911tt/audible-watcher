"""Shared fixtures: every test gets its own database, the Audible API is
mocked (tests never touch the network), and background enrichment runs
synchronously so assertions are deterministic."""
import os
import sys
import tempfile

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS_DIR)

# app.py initialises the database at import time — point it at a throwaway
# file so importing the app never touches a real watcher.db
os.environ["AUDIBLE_WATCHER_DB"] = os.path.join(tempfile.mkdtemp(),
                                                "import.db")

import mock_api  # noqa: E402
mock_api.install()

import checker  # noqa: E402
import db  # noqa: E402
import app as webapp  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """A brand-new database and clean mock state for every test."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "watcher.db"))
    db.init_db()
    mock_api.state.update(include_new=False, include_foreign=False)
    # keep enrichment out of background threads and skip its throttle sleeps;
    # tests that need metadata call the sync functions directly
    monkeypatch.setattr(checker, "background_enrichment_async", lambda: None)
    monkeypatch.setattr(checker.time, "sleep", lambda s: None)
    yield


@pytest.fixture
def client():
    return webapp.app.test_client()


@pytest.fixture
def watched():
    """An author watch and a series watch, baselined against the mock catalog
    (3 author books, 2 of them in the series)."""
    aid = db.add_watch("author", "Test Author", None)
    sid = db.add_watch("series", "Great Series", "SER123")
    n_author = checker.baseline_watch(aid)
    n_series = checker.baseline_watch(sid)
    return {"author": aid, "series": sid,
            "author_count": n_author, "series_count": n_series}
