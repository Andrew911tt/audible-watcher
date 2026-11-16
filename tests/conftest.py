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

# Never touch a real Audible login: a developer's own audible_auth.json may
# sit next to app.py, and tests must not sync their library or rewrite it.
import account  # noqa: E402
account.AUTH_FILE = os.path.join(tempfile.mkdtemp(), "no-auth.json")

import checker  # noqa: E402
import db  # noqa: E402
import app as webapp  # noqa: E402

import pytest  # noqa: E402
from flask.testing import FlaskClient  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """A brand-new database and clean mock state for every test."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "watcher.db"))
    monkeypatch.setattr(account, "AUTH_FILE", str(tmp_path / "auth.json"))
    db.init_db()
    mock_api.state.update(include_new=False, include_foreign=False)
    # keep enrichment out of background threads and skip its throttle sleeps;
    # tests that need metadata call the sync functions directly
    monkeypatch.setattr(checker, "background_enrichment_async", lambda: None)
    monkeypatch.setattr(checker.time, "sleep", lambda s: None)
    yield


TEST_CSRF_TOKEN = "test-csrf-token"


class CSRFClient(FlaskClient):
    """Test client that behaves like the app's own pages: every POST carries
    the session's CSRF token unless the test supplies its own."""

    def open(self, *args, **kwargs):
        data = kwargs.get("data")
        if (kwargs.get("method", "GET").upper() == "POST"
                and (data is None or isinstance(data, dict))
                and "csrf_token" not in (data or {})):
            kwargs["data"] = {**(data or {}), "csrf_token": TEST_CSRF_TOKEN}
        return super().open(*args, **kwargs)


@pytest.fixture
def client():
    c = CSRFClient(webapp.app, webapp.app.response_class, use_cookies=True)
    with c.session_transaction() as s:
        s["csrf_token"] = TEST_CSRF_TOKEN
    return c


@pytest.fixture
def raw_client():
    """A client with no CSRF token, like a forged cross-site request."""
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
