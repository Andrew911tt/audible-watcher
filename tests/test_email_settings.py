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


def test_settings_page_is_one_set_of_cards(client):
    html = client.get("/settings").get_data(as_text=True)
    main = html[html.index("<main>"):]
    titles = [main[i + len('class="panel-title">'):main.index("<", i + 20)]
              for i in range(len(main)) if main.startswith('class="panel-title">', i)]
    assert titles == ["Audible account", "Monitoring", "Email notifications",
                      "Access from other devices"]
    assert "<fieldset" not in main
    email = main[main.index('id="email"'):main.index('class="save-bar"')]
    assert 'formaction="/test-email"' in email          # test button inside
    assert "Troubleshooting" in email
    # Enter saves: the form's first submit button is Save, not the test email
    form = main[main.index('class="settings-form"'):]
    first = form[form.index("<button"):form.index("</button>")]
    assert "Save settings" in first and "formaction" not in first


def test_test_email_button_posts_from_the_settings_form(client, monkeypatch):
    sent = []
    monkeypatch.setattr(checker, "send_test_email", lambda s: sent.append(s))
    resp = client.post("/test-email", data={"smtp_host": "ignored",
                                            "check_hour": "8"})
    assert resp.status_code == 302 and len(sent) == 1
    assert sent[0]["smtp_host"] == db.get_settings()["smtp_host"]  # saved ones
