"""Audible account sync: owned library, pre-orders from the orders feed,
token persistence. The audible package is replaced by fakes; nothing is
sent to Amazon."""
import os

import pytest

import account
import db
from mock_api import make_product


class FakeAuth:
    saved = 0
    deregistered = 0
    fail_deregister = False
    customer_info = {"name": "Test Listener"}

    def to_file(self, path, encryption=False, set_default=True, **kw):
        FakeAuth.saved += 1
        with open(path, "w") as f:
            f.write("{}")

    def deregister_device(self, deregister_all=False):
        if FakeAuth.fail_deregister:
            raise ConnectionError("offline")
        FakeAuth.deregistered += 1


class FakeClient:
    def __init__(self, library, orders, fail_on=None):
        self.library, self.orders, self.fail_on = library, orders, fail_on
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, path, **params):
        self.calls.append((path, params))
        if self.fail_on == path:
            raise RuntimeError(f"{path} unavailable")
        if path == "1.0/library":
            start = (params["page"] - 1) * params["num_results"]
            return {"items": self.library[start:start + params["num_results"]]}
        if path == "1.0/orders":
            start = (params["page"] - 1) * params["num_results"]
            return {"orders": self.orders[start:start + params["num_results"]]}
        raise AssertionError(path)


@pytest.fixture
def fake_audible(monkeypatch, tmp_path):
    state = {"client": None}
    monkeypatch.setattr(account, "AUTH_FILE", str(tmp_path / "auth.json"))
    monkeypatch.setattr(account.audible.Authenticator, "from_file",
                        staticmethod(lambda path: FakeAuth()))
    monkeypatch.setattr(account.audible, "Client",
                        lambda auth: state["client"])
    monkeypatch.setattr(account.audible_api, "_fetch_by_asins",
                        lambda asins, mp="us": [
                            account.audible_api._simplify(
                                make_product(a, f"Pre-order {a}", ["W"],
                                             "2099-01-01"), mp)
                            for a in asins])
    FakeAuth.saved = 0
    FakeAuth.deregistered = 0
    FakeAuth.fail_deregister = False
    return state


def _order(asin, state="ORDER_CREATED"):
    return {"detail_state": state, "items": [{"product": {"asin": asin}}]}


def test_sync_library_with_preorders(fake_audible):
    library = [dict(make_product(f"L{i}", f"Owned {i}", ["W"], "2020-01-01"),
                    purchase_date="2021-05-06T10:00:00Z") for i in range(3)]
    orders = [_order("P1"), _order("L0"),  # L0 already delivered to library
              _order("P2", state="COMPLETE")]
    fake_audible["client"] = FakeClient(library, orders)
    assert account.sync_library() == 4
    owned = {b["asin"]: b for b in db.owned_books_list()}
    assert set(owned) == {"L0", "L1", "L2", "P1"}
    assert owned["P1"]["preordered"] == 1
    assert owned["P1"]["title"] == "Pre-order P1"
    assert owned["L0"]["purchase_date"] == "2021-05-06"
    assert db.get_settings().get("library_synced_at")


def test_library_and_orders_paginate(fake_audible):
    library = [make_product(f"L{i}", f"T{i}", ["W"], "2020-01-01")
               for i in range(501)]
    orders = [_order(f"P{i}") for i in range(60)]
    fake_audible["client"] = c = FakeClient(library, orders)
    assert account.sync_library() == 561
    pages = [(p, kw["page"]) for p, kw in c.calls]
    assert pages == [("1.0/library", 1), ("1.0/library", 2),
                     ("1.0/orders", 1), ("1.0/orders", 2)]


def test_orders_feed_failure_is_not_fatal(fake_audible):
    fake_audible["client"] = FakeClient(
        [make_product("L1", "T", ["W"], "2020-01-01")], [],
        fail_on="1.0/orders")
    assert account.sync_library() == 1


def test_tokens_saved_even_when_sync_fails(fake_audible):
    fake_audible["client"] = FakeClient([], [], fail_on="1.0/library")
    with pytest.raises(RuntimeError):
        account.sync_library()
    assert FakeAuth.saved == 1, "refreshed tokens must not be lost"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_auth_file_is_private(fake_audible):
    fake_audible["client"] = FakeClient([], [])
    account.sync_library()
    assert os.stat(account.AUTH_FILE).st_mode & 0o077 == 0


def test_token_file_written_atomically(fake_audible):
    fake_audible["client"] = FakeClient([], [])
    account.sync_library()
    assert os.path.exists(account.AUTH_FILE)
    assert not os.path.exists(account.AUTH_FILE + ".tmp")


def test_disconnect_deregisters_the_device(fake_audible):
    fake_audible["client"] = FakeClient(
        [make_product("L1", "T", ["W"], "2020-01-01")], [])
    account.sync_library()
    assert account.disconnect() is True
    assert FakeAuth.deregistered == 1
    assert not os.path.exists(account.AUTH_FILE)
    assert db.owned_count() == 0


def test_disconnect_offline_still_removes_local_data(fake_audible, client):
    fake_audible["client"] = FakeClient(
        [make_product("L1", "T", ["W"], "2020-01-01")], [])
    account.sync_library()
    FakeAuth.fail_deregister = True
    resp = client.post("/account/disconnect", follow_redirects=True)
    assert "Manage Your Content and" in resp.get_data(as_text=True)
    assert not os.path.exists(account.AUTH_FILE)
    assert db.owned_count() == 0


def test_login_flow(fake_audible, monkeypatch):
    """start_login hands out Amazon's URL; complete_login passes the pasted
    URL back to the waiting login thread, which saves the tokens."""
    seen = {}

    def from_login_external(locale, login_url_callback):
        seen["locale"] = locale
        seen["pasted"] = login_url_callback("https://amazon.example/ap/signin")
        return FakeAuth()
    monkeypatch.setattr(account.audible.Authenticator, "from_login_external",
                        staticmethod(from_login_external))
    url = account.start_login("uk")
    assert url == "https://amazon.example/ap/signin"
    assert account.pending_login_url() == url
    account.complete_login("  https://amazon.example/ap/maplanding?x=1 ")
    assert seen == {"locale": "uk",
                    "pasted": "https://amazon.example/ap/maplanding?x=1"}
    assert account.is_connected()
    assert account.pending_login_url() is None


def test_login_failure_is_reported(fake_audible, monkeypatch):
    def from_login_external(locale, login_url_callback):
        login_url_callback("https://amazon.example/ap/signin")
        raise ValueError("captcha required")
    monkeypatch.setattr(account.audible.Authenticator, "from_login_external",
                        staticmethod(from_login_external))
    account.start_login("us")
    with pytest.raises(RuntimeError, match="captcha required"):
        account.complete_login("https://amazon.example/ap/maplanding")
    assert not account.is_connected()


def test_complete_without_start_is_explained():
    account._pending = None
    with pytest.raises(RuntimeError, match="click Connect first"):
        account.complete_login("https://amazon.example/x")
