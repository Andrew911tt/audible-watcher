"""Email section of the Settings page: setup directions, status, friendly
errors."""
import smtplib
import socket
import ssl

import checker
import db

GMAIL = {"check_hour": "8", "smtp_host": "smtp.gmail.com",
         "smtp_port": "465", "smtp_security": "ssl",
         "smtp_user": "me@gmail.com"}


def test_unconfigured_page_shows_setup_steps(client):
    html = client.get("/settings").get_data(as_text=True)
    assert "Not set up yet" in html
    assert "https://myaccount.google.com/apppasswords" in html
    assert "https://myaccount.google.com/signinoptions/twosv" in html
    # the Gmail steps start expanded when email isn't set up
    assert '<details class="help-box" open>' in html
    assert "Troubleshooting" in html
    assert "smtp.mail.yahoo.com" in html  # other providers listed


def test_configured_page_shows_status(client):
    client.post("/settings", data={**GMAIL, "email_enabled": "on",
                                   "smtp_password": "abcdefghijklmnop"})
    html = client.get("/settings").get_data(as_text=True)
    assert "sending to" in html and "me@gmail.com" in html
    assert '<details class="help-box" open>' not in html  # steps collapsed


def test_app_password_spaces_are_removed(client):
    client.post("/settings", data={**GMAIL,
                                   "smtp_password": "abcd efgh ijkl mnop"})
    assert db.get_settings()["smtp_password"] == "abcdefghijklmnop"


def test_enabling_email_without_password_warns(client):
    resp = client.post("/settings", data={**GMAIL, "email_enabled": "on"},
                       follow_redirects=True)
    assert "app password is missing" in resp.get_data(as_text=True)


def test_test_email_failure_explains_the_fix(client, monkeypatch):
    def reject(*a, **k):
        raise smtplib.SMTPAuthenticationError(
            535, b"Username and Password not accepted")
    monkeypatch.setattr(checker, "send_email", reject)
    resp = client.post("/test-email", follow_redirects=True)
    html = resp.get_data(as_text=True)
    assert "Test email failed" in html and "app password" in html


def test_explain_smtp_error_hints():
    assert "port and security" in checker.explain_smtp_error(
        ssl.SSLError("WRONG_VERSION_NUMBER"))
    assert "server name" in checker.explain_smtp_error(
        socket.gaierror("Name or service not known"))
    assert checker.explain_smtp_error(ValueError("odd")) == "odd"
