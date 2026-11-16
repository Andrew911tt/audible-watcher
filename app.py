"""Audible Watcher — monitors Audible for new releases from your favorite
authors and series, shows them in a web GUI, and emails you when something
new appears.

Run:  python app.py          (then open http://localhost:5055)
"""
from __future__ import annotations

import logging
import os
import secrets
import threading
from datetime import date

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import (Flask, flash, redirect, render_template, request, session,
                   url_for)
from markupsafe import Markup

import account
import audible_api
import checker
import db
from paths import RESOURCE_DIR

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("watcher")

app = Flask(__name__,
            template_folder=os.path.join(RESOURCE_DIR, "templates"),
            static_folder=os.path.join(RESOURCE_DIR, "static"))
db.init_db()
# random per install, kept in the database so sessions survive restarts
app.secret_key = db.secret_key()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

_check_lock = threading.Lock()
scheduler = BackgroundScheduler()

DEFAULT_CHECK_HOUR = 8


# ------------------------------------------------------------------ CSRF
# There is no login, so without this any web page the user visits could
# make their browser POST here (change settings, send the saved SMTP
# password to another server via "Send test email", delete watches...).
# Every state-changing request must carry the token from the user's session.

def csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = session["csrf_token"] = secrets.token_urlsafe(32)
    return token


def csrf_field() -> Markup:
    return Markup('<input type="hidden" name="csrf_token" value="%s">'
                  % csrf_token())


app.jinja_env.globals["csrf_field"] = csrf_field


@app.before_request
def csrf_protect():
    if request.method != "POST":
        return None
    sent = (request.form.get("csrf_token")
            or request.headers.get("X-CSRF-Token") or "")
    expected = session.get("csrf_token") or ""
    if expected and secrets.compare_digest(sent, expected):
        return None
    log.warning("Rejected POST %s without a valid CSRF token", request.path)
    msg = ("This request didn't come from an Audible Watcher page (or your "
           "session expired). Reload the page and try again.")
    if request.headers.get("X-Requested-With") == "fetch":
        return {"ok": False, "error": msg}, 400
    return msg, 400, {"Content-Type": "text/plain; charset=utf-8"}


@app.context_processor
def inject_today():
    # for marking unreleased/pre-order books (release date in the future/TBA)
    return {"today": date.today().isoformat()}


@app.context_processor
def inject_owned():
    # asins the user owns (empty set until an Audible account is connected)
    return {"owned_asins": db.owned_asins()}


@app.context_processor
def inject_watch_lookup():
    """watch_id_for(type, name): the existing watch's id, or None."""
    by_key = {(w["type"], w["name"].strip().lower()): w["id"]
              for w in db.list_watches()}

    def watch_id_for(wtype, name):
        return by_key.get((wtype, (name or "").strip().lower()))

    return {"watch_id_for": watch_id_for}


@app.context_processor
def inject_author_url():
    """author_url(name): the author's watch page if watched, else an
    in-app author search for them."""
    watched = {w["name"].strip().lower(): w["id"]
               for w in db.list_watches() if w["type"] == "author"}

    def author_url(name):
        name = name.strip()
        watch_id = watched.get(name.lower())
        if watch_id:
            return url_for("watch_detail", watch_id=watch_id)
        return url_for("search", q=name, type="author")

    return {"author_url": author_url}


def scheduled_check():
    if _check_lock.acquire(blocking=False):
        try:
            checker.check_all(trigger="scheduled")
        finally:
            _check_lock.release()


def _parse_hour(value) -> int | None:
    """A valid daily-check hour (0-23), or None."""
    value = str(value or "").strip()
    if value.isdigit() and 0 <= int(value) <= 23:
        return int(value)
    return None


def reschedule():
    raw = db.get_settings().get("check_hour")
    hour = _parse_hour(raw)
    if hour is None:  # never let a bad stored value stop the app starting
        log.warning("Invalid check_hour %r in settings; using %02d:00",
                    raw, DEFAULT_CHECK_HOUR)
        hour = DEFAULT_CHECK_HOUR
    scheduler.remove_all_jobs()
    scheduler.add_job(scheduled_check, CronTrigger(hour=hour, minute=0),
                      id="daily_check")
    log.info("Daily check scheduled for %02d:00 local time", hour)


# ------------------------------------------------------------------ pages

@app.route("/")
def dashboard():
    tab = request.args.get("tab", "authors")
    if tab not in ("authors", "series", "preorders", "library"):
        tab = "authors"
    view = request.args.get("view", "all")
    if view not in ("all", "author", "series"):
        view = "all"
    sort = request.args.get("sort", "recent")
    if sort not in ("recent", "newest", "largest", "owned"):
        sort = "recent"
    watches = db.list_watches()
    fresh = db.new_books()
    checks = db.recent_checks(5)
    settings = db.get_settings()
    owned = db.owned_books_list() if tab == "library" else []
    return render_template(
        "dashboard.html", tab=tab, view=view, sort=sort, fresh=fresh,
        checks=checks, settings=settings,
        authors=[w for w in watches if w["type"] == "author"],
        series=[w for w in watches if w["type"] == "series"],
        preorders=db.preorder_books(),
        owned=owned, owned_total=db.owned_count(),
        owned_groups=_group_owned(owned, view, sort))


def _seq_key(b: dict) -> tuple:
    """Sort key for a book's position in its series (handles '3.5' and
    multi-sequence strings like '1.5, 4.4'; unnumbered books go last)."""
    s = (b.get("series_sequence") or "").split(",")[0].strip()
    try:
        return (0, float(s))
    except ValueError:
        return (1, 0.0)


def _group_owned(owned: list[dict], view: str,
                 sort: str = "recent") -> list[tuple] | None:
    """Group the owned library for the Library tab's sub-views."""
    if view == "author":
        groups: dict[str, list] = {}
        for b in owned:
            key = (b["authors"] or "").split(",")[0].strip() or "Unknown"
            groups.setdefault(key, []).append(b)
        for grp in groups.values():  # newest release first within an author
            grp.sort(key=lambda b: b["release_date"] or "9999", reverse=True)
        return sorted(groups.items(), key=lambda kv: kv[0].lower())
    if view == "series":
        groups = {}
        for b in owned:
            groups.setdefault(b["series_title"] or "Standalone", []).append(b)
        for grp in groups.values():  # book order within a series
            grp.sort(key=lambda b: (_seq_key(b), b["release_date"] or ""))
        if sort == "newest":       # series with the newest book first
            def key(kv):
                return max((b["release_date"] or "9999") for b in kv[1])
        elif sort == "largest":    # biggest series first (full catalog size)
            sizes = db.series_sizes()
            def key(kv):
                return max(sizes.get(kv[0], 0), len(kv[1]))
        elif sort == "owned":      # series you own the most of first
            def key(kv):
                return len(kv[1])
        else:                      # most recent purchase first
            def key(kv):
                return max((b["purchase_date"] or "") for b in kv[1])
        ordered = sorted(groups.items(), key=key, reverse=True)
        ordered.sort(key=lambda kv: kv[0] == "Standalone")  # always last
        return ordered
    return None


@app.route("/watch/<int:watch_id>")
def watch_detail(watch_id):
    w = db.get_watch(watch_id)
    if not w:
        flash("Watch not found", "error")
        return redirect(url_for("dashboard"))
    books = db.books_for_watch(watch_id)
    hidden_books = [b for b in db.books_for_watch(watch_id, include_hidden=True)
                    if b["hidden"]]
    view = request.args.get("view", "all")
    series_groups = None
    if view == "series":
        groups: dict[str, list] = {}
        for b in books:  # already newest-first within the list
            groups.setdefault(b["series_title"] or "Standalone", []).append(b)
        # series ordered by their individually newest book
        series_groups = sorted(
            groups.items(),
            key=lambda kv: max((b["release_date"] or "9999") for b in kv[1]),
            reverse=True)
    return render_template("watch.html", watch=w, books=books,
                           hidden_books=hidden_books, view=view,
                           series_groups=series_groups)


@app.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    results, series_results = [], []
    if q:
        settings = db.get_settings()
        mp = settings.get("marketplace", "us")
        lang = (settings.get("language") or "").strip().lower()
        try:
            # one query, both kinds of matches — the results speak for
            # themselves (an author name yields authors, a series name series)
            books = audible_api.search_by_author(q, mp, max_results=20)
            books = [b for b in books
                     if audible_api.matches_language(b, lang)]
            names = {}
            for b in books:
                for name in (b["authors"] or "").split(","):
                    name = name.strip()
                    if name and q.lower() in name.lower():
                        names.setdefault(name, {"name": name, "count": 0,
                                                "sample": b})
                        names[name]["count"] += 1
            results = sorted(names.values(), key=lambda x: -x["count"])
            series_results = audible_api.search_series_candidates(
                q, mp, language=lang)
        except audible_api.AudibleAPIError as e:
            flash(f"Audible search failed: {e}", "error")
    return render_template("search.html", q=q,
                           results=results, series_results=series_results)


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    if request.method == "POST":
        keys = ["marketplace", "language", "check_hour", "smtp_host",
                "smtp_port", "smtp_security", "smtp_user", "email_from",
                "email_to"]
        values = {k: request.form.get(k, "").strip() for k in keys}
        if _parse_hour(values["check_hour"]) is None:
            flash("Daily check time must be a whole hour from 0 to 23 — "
                  "settings not saved", "error")
            return redirect(url_for("settings_page"))
        values["check_hour"] = str(_parse_hour(values["check_hour"]))
        current = db.get_settings()
        pw = request.form.get("smtp_password", "")
        if pw:  # blank = keep existing...
            values["smtp_password"] = pw
        elif (values["smtp_host"] != current.get("smtp_host")
              or values["smtp_user"] != current.get("smtp_user")):
            # ...but only for the server/account it was entered for, so the
            # saved app password can never be redirected to another server
            values["smtp_password"] = ""
            if current.get("smtp_password"):
                flash("Mail server or username changed — enter the app "
                      "password again", "error")
        values["email_enabled"] = "1" if request.form.get("email_enabled") else "0"
        db.save_settings(values)
        reschedule()
        flash("Settings saved", "ok")
        return redirect(url_for("settings_page"))
    return render_template("settings.html", settings=db.get_settings(),
                           marketplaces=sorted(audible_api.MARKETPLACES),
                           languages=audible_api.LANGUAGES)


# ------------------------------------------------------------------ actions

def _primary_author(watch_id: int) -> str | None:
    """Most common author name among a watch's visible books."""
    counts: dict[str, int] = {}
    for b in db.books_for_watch(watch_id):
        for name in (b["authors"] or "").split(","):
            name = name.strip()
            if name:
                counts[name] = counts.get(name, 0) + 1
    return max(counts, key=counts.get) if counts else None


@app.route("/watch/add", methods=["POST"])
def add_watch():
    # forms with class js-watch submit via fetch() and expect JSON back,
    # so the page the user is on never reloads
    wants_json = request.headers.get("X-Requested-With") == "fetch"
    wtype = request.form.get("type", "author")
    name = (request.form.get("name") or "").strip()
    asin = request.form.get("asin") or None
    if not name:
        if wants_json:
            return {"ok": False, "error": "Name is required"}, 400
        flash("Name is required", "error")
        return redirect(request.referrer or url_for("dashboard"))

    notes = []
    watch_id = db.add_watch(wtype, name, asin)
    try:
        n = checker.baseline_watch(watch_id)
        notes.append((f"Now watching {wtype} “{name}” — found {n} existing "
                      "title(s). You'll be notified about anything newer.",
                      "ok"))
    except Exception as e:  # noqa: BLE001
        notes.append((f"Added “{name}” but the initial fetch failed: {e}",
                      "error"))

    # watching a series implies watching its author for anything else they write
    if wtype == "series":
        author = _primary_author(watch_id)
        existing = {(w["type"], w["name"].lower()) for w in db.list_watches()}
        if author and ("author", author.lower()) not in existing:
            author_id = db.add_watch("author", author, None)
            try:
                n = checker.baseline_watch(author_id)
                notes.append((f"Also watching author “{author}” — "
                              f"found {n} existing title(s).", "ok"))
            except Exception as e:  # noqa: BLE001
                notes.append((f"Added author “{author}” but the initial "
                              f"fetch failed: {e}", "error"))

    if wants_json:
        return {"ok": True,
                "watch_url": url_for("watch_detail", watch_id=watch_id),
                "message": " ".join(m for m, _ in notes)}
    for msg, cat in notes:
        flash(msg, cat)
    return redirect(url_for("dashboard"))


@app.route("/watch/add-bulk", methods=["POST"])
def add_watch_bulk():
    added = 0
    for item in request.form.getlist("items"):
        wtype, _, name = item.partition("::")
        if wtype in ("author", "series") and name:
            wid = db.add_watch(wtype, name, None)
            try:
                checker.baseline_watch(wid)
            except Exception:  # noqa: BLE001
                log.exception("Baseline failed for %s", name)
            added += 1
    flash(f"Added {added} watch(es) from your OpenAudible library", "ok")
    return redirect(url_for("dashboard"))


@app.route("/watch/<int:watch_id>/delete", methods=["POST"])
def delete_watch(watch_id):
    db.remove_watch(watch_id)
    flash("Watch removed", "ok")
    return redirect(url_for("dashboard"))


@app.route("/check-now", methods=["POST"])
def check_now():
    if not _check_lock.acquire(blocking=False):
        flash("A check is already running", "error")
        return redirect(url_for("dashboard"))
    try:
        result = checker.check_all(trigger="manual")
    finally:
        _check_lock.release()
    if result["errors"]:
        flash("Check finished with errors: " + "; ".join(result["errors"]),
              "error")
    msg = f"Check complete — {result['new_books']} new book(s) found"
    if result["email_sent"]:
        msg += ", email sent"
    flash(msg, "ok")
    return redirect(url_for("dashboard"))


@app.route("/book/<asin>/<int:watch_id>/seen", methods=["POST"])
def book_seen(asin, watch_id):
    db.mark_seen(asin, watch_id)
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/book/<asin>/<int:watch_id>/hide", methods=["POST"])
def book_hide(asin, watch_id):
    db.set_book_hidden(asin, watch_id, True)
    return redirect(request.referrer or url_for("watch_detail",
                                                watch_id=watch_id))


@app.route("/book/<asin>/<int:watch_id>/unhide", methods=["POST"])
def book_unhide(asin, watch_id):
    db.set_book_hidden(asin, watch_id, False)
    return redirect(request.referrer or url_for("watch_detail",
                                                watch_id=watch_id))


@app.route("/watch/<int:watch_id>/hide-duplicates", methods=["POST"])
def hide_duplicates(watch_id):
    n = db.hide_duplicate_books(watch_id)
    if n:
        flash(f"Cleaned up {n} duplicate entr{'y' if n == 1 else 'ies'} — "
              "they're in the hidden section below if you want any back", "ok")
    else:
        flash("No duplicates found", "ok")
    return redirect(url_for("watch_detail", watch_id=watch_id))


@app.route("/seen-all", methods=["POST"])
def seen_all():
    db.mark_all_seen()
    flash("All new books marked as seen", "ok")
    return redirect(url_for("dashboard"))


@app.route("/account")
def account_page():
    return render_template(
        "account.html", connected=account.is_connected(),
        name=account.account_name() if account.is_connected() else None,
        login_url=account.pending_login_url(),
        owned_count=db.owned_count(),
        synced_at=db.get_settings().get("library_synced_at"))


@app.route("/account/start", methods=["POST"])
def account_start():
    mp = db.get_settings().get("marketplace", "us")
    try:
        account.start_login(mp)
    except Exception as e:  # noqa: BLE001
        flash(f"Could not start the Amazon login: {e}", "error")
    return redirect(url_for("account_page"))


@app.route("/account/complete", methods=["POST"])
def account_complete():
    url = (request.form.get("response_url") or "").strip()
    if not url:
        flash("Paste the full URL of the page Amazon left you on", "error")
        return redirect(url_for("account_page"))
    try:
        account.complete_login(url)
        n = account.sync_library()
        flash(f"Audible account connected — {n} owned title(s) synced", "ok")
    except Exception as e:  # noqa: BLE001
        flash(f"Connecting failed: {e}", "error")
    return redirect(url_for("account_page"))


@app.route("/account/sync", methods=["POST"])
def account_sync():
    try:
        n = account.sync_library()
        flash(f"Library synced — {n} owned title(s)", "ok")
    except Exception as e:  # noqa: BLE001
        flash(f"Library sync failed: {e}", "error")
    return redirect(url_for("account_page"))


@app.route("/account/disconnect", methods=["POST"])
def account_disconnect():
    account.disconnect()
    flash("Audible account disconnected and owned-library data removed. "
          "You can also deregister the device under Amazon's "
          "“Manage Your Content and Devices”.", "ok")
    return redirect(url_for("account_page"))


@app.route("/test-email", methods=["POST"])
def test_email():
    try:
        checker.send_test_email(db.get_settings())
        flash("Test email sent — check your inbox", "ok")
    except Exception as e:  # noqa: BLE001
        flash(f"Test email failed: {e}", "error")
    return redirect(url_for("settings_page"))


HOST = os.environ.get("AW_HOST", "127.0.0.1")  # 0.0.0.0 = reachable on the LAN
PORT = int(os.environ.get("AW_PORT", "5055"))


def start_scheduler():
    """Start the daily check. Call once, before serving."""
    reschedule()
    scheduler.start()


if __name__ == "__main__":
    start_scheduler()
    try:  # production server if installed (Docker, desktop build)
        from waitress import serve
        log.info("Audible Watcher on http://%s:%s", HOST, PORT)
        serve(app, host=HOST, port=PORT, threads=8)
    except ImportError:
        app.run(host=HOST, port=PORT, debug=False)
