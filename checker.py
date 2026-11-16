"""Release checking and email notification logic."""
from __future__ import annotations

import logging
import smtplib
import ssl
import threading
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import audible_api
import bookinfo
import db

log = logging.getLogger(__name__)


_enrich_lock = threading.Lock()


def background_enrichment_async():
    """Run the metadata passes (marketplace availability, publish dates) in
    the background so adding a watch or running a check never blocks on
    them. Skips silently if a batch is already running."""
    def run():
        if not _enrich_lock.acquire(blocking=False):
            return
        try:
            verify_market_availability()
            enrich_published_dates()
        finally:
            _enrich_lock.release()

    threading.Thread(target=run, daemon=True).start()


def verify_market_availability(limit: int = 50):
    """Check not-yet-validated books against the marketplace's own store
    listing and hide editions that aren't sold there (series relationships
    include foreign-region editions like UK printings)."""
    marketplace = db.get_settings().get("marketplace", "us")
    for r in db.books_missing_availability(limit):
        author = (r["authors"] or "").split(",")[0].strip() or None
        try:
            ok = audible_api.check_market_availability(
                r["asin"], r["title"], author, marketplace)
        except audible_api.AudibleAPIError:
            log.info("Availability checks unavailable — deferring the rest")
            break
        if ok is False:
            db.set_availability(r["asin"], False)
            db.hide_asin_everywhere(r["asin"])
            log.info("Hidden %r (%s) — not sold on this marketplace",
                     r["title"], r["asin"])
        else:
            # present, or search couldn't say: keep it and stop rechecking
            db.set_availability(r["asin"], True)
        time.sleep(0.3)  # be polite


def enrich_published_dates(limit: int = 20):
    """Look up original (print/ebook) publish dates for books that don't have
    one yet — a bounded, throttled batch per run so checks stay fast and the
    sources aren't hammered."""
    for r in db.books_missing_published(limit):
        author = (r["authors"] or "").split(",")[0].strip() or None
        try:
            pd, definitive = bookinfo.published_date(r["title"], author)
        except bookinfo.RateLimited:
            log.info("Publish-date sources unavailable — deferring the rest "
                     "of this batch to the next check")
            break
        except Exception:  # noqa: BLE001 - enrichment must never break checks
            log.exception("Publish-date lookup failed for %r", r["title"])
            break
        if pd or definitive:
            db.set_published_date(r["asin"], pd or "")
        time.sleep(0.5)  # be polite to free APIs


def _split_by_language(books: list[dict], settings: dict) -> tuple[list, list]:
    """Partition fetched books into (kept, foreign) per the language setting."""
    language = (settings.get("language") or "").strip().lower()
    keep, foreign = [], []
    for b in books:
        (keep if audible_api.matches_language(b, language) else foreign).append(b)
    return keep, foreign


def _store_foreign(watch_id: int, foreign: list[dict]):
    """Record wrong-language editions as hidden: invisible, never alerted,
    but remembered so checks don't re-flag them as new releases."""
    if foreign:
        db.upsert_books(watch_id, foreign, as_new=False, hidden=True)
        db.hide_books(watch_id, [b["asin"] for b in foreign])


def check_all(trigger: str = "manual") -> dict:
    """Check every watch for new books, send one digest email if any found."""
    settings = db.get_settings()
    marketplace = settings.get("marketplace", "us")
    total_new = 0
    errors = []

    for w in db.list_watches():
        try:
            books = audible_api.fetch_watch_books(
                w["type"], w["name"], w["asin"], marketplace)
            keep, foreign = _split_by_language(books, settings)
            _store_foreign(w["id"], foreign)
            fresh = db.upsert_books(w["id"], keep, as_new=True)
            total_new += len(fresh)
            db.mark_watch_checked(w["id"], None)
            if fresh:
                log.info("Watch %r: %d new book(s)", w["name"], len(fresh))
        except Exception as e:  # noqa: BLE001 - keep checking other watches
            log.exception("Check failed for watch %r", w["name"])
            errors.append(f"{w['name']}: {e}")
            db.mark_watch_checked(w["id"], str(e))

    background_enrichment_async()

    email_sent = False
    pending = db.unnotified_books()
    if pending and settings.get("email_enabled") == "1":
        try:
            send_new_release_email(db.dedupe_by_asin(pending), settings)
            db.mark_notified([(b["asin"], b["watch_id"]) for b in pending])
            email_sent = True
        except Exception as e:  # noqa: BLE001
            log.exception("Email send failed")
            errors.append(f"email: {e}")

    db.log_check(trigger, total_new, "; ".join(errors) or None, email_sent)
    return {"new_books": total_new, "errors": errors, "email_sent": email_sent}


def baseline_watch(watch_id: int) -> int:
    """Initial fetch for a newly added watch; books stored as already-seen."""
    w = db.get_watch(watch_id)
    if not w:
        return 0
    settings = db.get_settings()
    books = audible_api.fetch_watch_books(
        w["type"], w["name"], w["asin"], settings.get("marketplace", "us"))
    keep, foreign = _split_by_language(books, settings)
    _store_foreign(watch_id, foreign)
    inserted = db.upsert_books(watch_id, keep, as_new=False)
    db.mark_watch_checked(watch_id, None)
    background_enrichment_async()
    return len(inserted)


# ---------- email ----------

def _book_row_html(b: dict) -> str:
    seq = f" (Book {b['series_sequence']})" if b.get("series_sequence") else ""
    series = f"<br><em>{b['series_title']}{seq}</em>" if b.get("series_title") else ""
    date = b.get("release_date") or "TBA"
    img = (f'<img src="{b["image_url"]}" width="80" '
           'style="border-radius:6px;vertical-align:top">' if b.get("image_url") else "")
    return f"""
    <tr>
      <td style="padding:10px 12px 10px 0">{img}</td>
      <td style="padding:10px 0">
        <a href="{b['url']}" style="font-size:16px;font-weight:bold;
           color:#f29d38;text-decoration:none">{b['title']}</a><br>
        by {b.get('authors') or 'Unknown'}{series}<br>
        <span style="color:#666">Release date: {date}</span><br>
        <span style="color:#999;font-size:12px">Watched: {b['watch_name']}
        ({b['watch_type']})</span>
      </td>
    </tr>"""


def build_email(books: list[dict]) -> tuple[str, str, str]:
    n = len(books)
    subject = (f"Audible Watcher: {n} new book{'s' if n != 1 else ''} "
               "from your favorites")
    rows = "\n".join(_book_row_html(b) for b in books)
    html = f"""<html><body style="font-family:Arial,Helvetica,sans-serif;
      color:#222;max-width:640px">
      <h2 style="color:#f29d38">🎧 New on Audible</h2>
      <p>{n} new title{'s' if n != 1 else ''} matching your watched authors
      and series:</p>
      <table cellspacing="0" cellpadding="0">{rows}</table>
      <p style="color:#999;font-size:12px;margin-top:24px">
        Sent by your Audible Watcher app.</p>
    </body></html>"""
    text_lines = [f"{n} new Audible title(s) from your favorites:", ""]
    for b in books:
        text_lines.append(f"- {b['title']} by {b.get('authors') or 'Unknown'}"
                          f" — releases {b.get('release_date') or 'TBA'}")
        text_lines.append(f"  {b['url']}")
    return subject, html, "\n".join(text_lines)


def send_email(subject: str, html: str, text: str, settings: dict):
    host = settings["smtp_host"]
    port = int(settings["smtp_port"] or 465)
    user = settings["smtp_user"]
    password = settings["smtp_password"]
    sender = settings["email_from"] or user
    recipients = [r.strip() for r in (settings["email_to"] or user).split(",")
                  if r.strip()]
    if not user or not password:
        raise RuntimeError("SMTP username/app password not configured "
                           "(see Settings page)")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    if settings.get("smtp_security", "ssl") == "starttls":
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(user, password)
            s.sendmail(sender, recipients, msg.as_string())
    else:
        with smtplib.SMTP_SSL(host, port, timeout=30,
                              context=ssl.create_default_context()) as s:
            s.login(user, password)
            s.sendmail(sender, recipients, msg.as_string())


def send_new_release_email(books: list[dict], settings: dict):
    subject, html, text = build_email(books)
    send_email(subject, html, text, settings)


def send_test_email(settings: dict):
    send_email(
        "Audible Watcher: test email",
        "<p>✅ Your Audible Watcher email settings work.</p>",
        "Your Audible Watcher email settings work.",
        settings,
    )
