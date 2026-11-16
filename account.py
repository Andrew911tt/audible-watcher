"""Audible account connection for owned-library sync, via the `audible`
package's external-browser login.

You sign in on amazon.com in your own browser — the app never sees your
credentials. It stores only the resulting device tokens (audible_auth.json
next to the database), which you can revoke at any time from Amazon's
"Manage Your Content and Devices" page.
"""
from __future__ import annotations

import logging
import os
import threading

import audible

import audible_api
import db
from paths import DATA_DIR

log = logging.getLogger(__name__)

AUTH_FILE = os.path.join(DATA_DIR, "audible_auth.json")

# our marketplace codes happen to match the audible package's locales
LOCALES = {"us", "uk", "ca", "au", "de", "fr", "in", "it", "es", "jp"}


class _PendingLogin:
    """State shared between the web routes and the login worker thread.

    Authenticator.from_login_external drives the whole login in one call and
    asks for the pasted response URL via a callback, so the flow runs in a
    thread that blocks between the two web requests (start / complete).
    """

    def __init__(self):
        self.login_url: str | None = None
        self.url_ready = threading.Event()
        self.response_url: str | None = None
        self.response_ready = threading.Event()
        self.done = threading.Event()
        self.error: str | None = None


_pending: _PendingLogin | None = None


def is_connected() -> bool:
    return os.path.exists(AUTH_FILE)


def pending_login_url() -> str | None:
    return _pending.login_url if _pending else None


def start_login(marketplace: str) -> str:
    """Begin the external-browser login; returns the amazon.com sign-in URL."""
    global _pending
    locale = marketplace if marketplace in LOCALES else "us"
    p = _PendingLogin()

    def url_callback(url: str) -> str:
        p.login_url = url
        p.url_ready.set()
        if not p.response_ready.wait(timeout=900):
            raise TimeoutError("login was not completed within 15 minutes")
        return p.response_url

    def run():
        try:
            auth = audible.Authenticator.from_login_external(
                locale=locale, login_url_callback=url_callback)
            auth.to_file(AUTH_FILE, encryption=False)
        except Exception as e:  # noqa: BLE001 - surfaced via p.error
            log.exception("Audible login failed")
            p.error = str(e)
        finally:
            p.done.set()

    threading.Thread(target=run, daemon=True).start()
    if not p.url_ready.wait(timeout=30):
        raise RuntimeError("could not build the Amazon sign-in URL"
                           + (f" — {p.error}" if p.error else ""))
    _pending = p
    return p.login_url


def complete_login(response_url: str, timeout: int = 90):
    """Hand the pasted post-login URL to the waiting login thread."""
    global _pending
    p = _pending
    if not p:
        raise RuntimeError("no login in progress — click Connect first")
    p.response_url = response_url.strip()
    p.response_ready.set()
    if not p.done.wait(timeout=timeout):
        raise RuntimeError("login did not finish in time — try again")
    _pending = None
    if p.error:
        raise RuntimeError(p.error)


def disconnect():
    """Forget the device tokens and owned-library data. (The device can also
    be deregistered on Amazon's 'Manage Your Content and Devices' page.)"""
    global _pending
    _pending = None
    if os.path.exists(AUTH_FILE):
        os.remove(AUTH_FILE)
    db.replace_owned([])


def account_name() -> str | None:
    if not is_connected():
        return None
    try:
        auth = audible.Authenticator.from_file(AUTH_FILE)
        return (auth.customer_info or {}).get("name")
    except Exception:  # noqa: BLE001
        log.exception("Could not read auth file")
        return None


def _preordered_asins(client) -> list[str]:
    """ASINs of placed-but-undelivered orders (pre-orders). Audible keeps
    pre-orders in the orders feed until release day — they only move into
    the library once they're delivered."""
    asins, page = [], 1
    while page <= 5:
        data = client.get("1.0/orders", num_results=50, page=page)
        orders = data.get("orders") or []
        if not orders:
            break
        for o in orders:
            if o.get("detail_state") != "ORDER_CREATED":
                continue
            for it in o.get("items") or []:
                asin = (it.get("product") or {}).get("asin") or it.get("asin")
                if asin:
                    asins.append(asin)
        if len(orders) < 50:
            break
        page += 1
    return asins


def sync_library() -> int:
    """Fetch the full owned library (plus pending pre-orders) into the
    database; returns title count."""
    auth = audible.Authenticator.from_file(AUTH_FILE)
    marketplace = db.get_settings().get("marketplace", "us")
    items = []
    with audible.Client(auth=auth) as client:
        page = 1
        while True:
            data = client.get(
                "1.0/library", num_results=500, page=page,
                response_groups="product_desc,product_attrs,contributors,"
                                "series,media")
            batch = data.get("items") or []
            items.extend(batch)
            if len(batch) < 500:
                break
            page += 1
        try:
            preordered = _preordered_asins(client)
        except Exception:  # noqa: BLE001 - orders feed is best-effort
            log.exception("Could not read the orders feed for pre-orders")
            preordered = []
    auth.to_file(AUTH_FILE, encryption=False)  # persist refreshed tokens

    rows = []
    for i in items:
        b = audible_api._simplify(i, marketplace)
        b["purchase_date"] = (i.get("purchase_date") or "")[:10] or None
        rows.append(b)

    # pre-orders aren't in the library yet — pull their details from the
    # public catalog so they show up properly
    in_library = {b["asin"] for b in rows}
    pending = [a for a in preordered if a not in in_library]
    if pending:
        details = {}
        try:
            details = {b["asin"]: b for b in
                       audible_api._fetch_by_asins(pending, marketplace)}
        except audible_api.AudibleAPIError:
            log.warning("Catalog lookup for pre-ordered titles failed")
        for asin in pending:
            b = details.get(asin) or {"asin": asin}
            b["preordered"] = 1
            rows.append(b)

    db.replace_owned(rows)
    db.save_settings({"library_synced_at": db.now_iso()})
    return len(rows)
