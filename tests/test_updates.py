"""Check for updates (1.1.0). GitHub is never contacted: every test supplies
the answer _fetch would get."""
import os
import re
from datetime import datetime, timedelta, timezone

import pytest

import app as webapp
import db
import updates
from version import __version__

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeResponse:
    def __init__(self, status=200, data=None, etag='"abc"', bad_json=False):
        self.status_code = status
        self._data = data
        self._bad = bad_json
        self.headers = {"ETag": etag} if etag else {}

    def json(self):
        if self._bad:
            raise ValueError("not JSON")
        return self._data


def release(tag, exe=True):
    return {"tag_name": tag,
            "html_url": f"https://github.com/{updates.REPO}/releases/tag/{tag}",
            "assets": ([{"name": f"AudibleWatcher-Setup-{tag[1:]}.exe",
                         "browser_download_url":
                         f"https://github.com/{updates.REPO}/releases/"
                         f"download/{tag}/AudibleWatcher-Setup.exe"}]
                       if exe else [])}


@pytest.fixture
def github(monkeypatch):
    """Answers to hand out, and the ETags each request sent."""
    calls = {"answers": [], "etags": []}

    def fake(etag):
        calls["etags"].append(etag)
        answer = calls["answers"].pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(updates, "_fetch", fake)
    return calls


def bump(n=1):
    parts = [int(p) for p in __version__.split(".")]
    parts[-1] += n
    return "v" + ".".join(map(str, parts))


# ------------------------------------------------------------- versions

def test_version_comparison_is_numeric():
    assert updates.is_newer("1.0.10", "1.0.9")
    assert updates.is_newer("v2.0.0", "1.9.9")
    assert not updates.is_newer("1.0.9", "1.0.10")
    assert not updates.is_newer("1.1.0", "1.1.0")
    assert not updates.is_newer("1.2.0-beta", "1.1.0")   # not a plain number
    assert updates.parse_version("v1.0.10") == (1, 0, 10)


@pytest.mark.parametrize("tag,available", [(bump(), True),
                                           ("v" + __version__, False),
                                           ("v0.0.1", False)])
def test_newer_same_older(github, tag, available):
    github["answers"].append(FakeResponse(data=release(tag)))
    info = updates.check(force=True)
    assert info["available"] is available
    assert info["latest"] == tag[1:] and info["error"] is None
    assert info["checked_at"]


def test_download_link_is_the_installer(github):
    github["answers"].append(FakeResponse(data=release(bump())))
    info = updates.check(force=True)
    assert info["download"].endswith(".exe")
    assert info["url"].startswith("https://github.com/")


def test_links_from_anywhere_else_are_not_used(github):
    data = release(bump())
    data["html_url"] = "https://evil.example/x"
    data["assets"][0]["browser_download_url"] = "https://evil.example/a.exe"
    github["answers"].append(FakeResponse(data=data))
    info = updates.check(force=True)
    assert info["url"] == updates.RELEASES_PAGE
    assert info["download"] is None


@pytest.mark.parametrize("answer,reason", [
    (updates.requests.ConnectionError("down"), "couldn't reach GitHub"),
    (updates.requests.Timeout("slow"), "couldn't reach GitHub"),
    (FakeResponse(403), "limit"),
    (FakeResponse(429), "limit"),
    (FakeResponse(404), "no published release"),
    (FakeResponse(500), "HTTP 500"),
    (FakeResponse(bad_json=True), "wasn't understood"),
    (FakeResponse(data=["not", "a", "dict"]), "wasn't understood"),
    (FakeResponse(data={"tag_name": "latest"}), "wasn't understood"),
])
def test_failures_never_raise_and_keep_the_last_answer(github, answer,
                                                       reason):
    github["answers"] += [FakeResponse(data=release(bump())), answer]
    updates.check(force=True)
    info = updates.check(force=True)
    assert reason in info["error"]
    assert info["latest"] == bump()[1:]             # still known
    # a good answer clears the error
    github["answers"].append(FakeResponse(data=release(bump())))
    assert updates.check(force=True)["error"] is None


def test_etag_not_modified(github):
    github["answers"] += [FakeResponse(data=release(bump()), etag='"v1"'),
                          FakeResponse(304, etag=None)]
    updates.check(force=True)
    info = updates.check(force=True)
    assert github["etags"] == [None, '"v1"']
    assert info["latest"] == bump()[1:] and info["error"] is None


def test_answers_are_cached_for_12_hours(github, monkeypatch):
    github["answers"].append(FakeResponse(data=release(bump())))
    updates.check()
    updates.check()                                 # cached: no request
    assert len(github["etags"]) == 1
    later = datetime.now(timezone.utc) + timedelta(hours=13)
    monkeypatch.setattr(updates, "_now", lambda: later)
    github["answers"].append(FakeResponse(data=release(bump(2))))
    assert updates.check()["latest"] == bump(2)[1:]


def test_install_types(monkeypatch, tmp_path):
    monkeypatch.setattr(updates.os.path, "exists", lambda p: False)
    monkeypatch.setattr(updates, "RESOURCE_DIR", str(tmp_path))
    assert updates.install_type() == "manual"
    (tmp_path / ".git").mkdir()
    assert updates.install_type() == "git"
    monkeypatch.setattr(updates.os.path, "exists",
                        lambda p: p == "/.dockerenv")
    assert updates.install_type() == "docker"
    monkeypatch.setattr(updates.sys, "frozen", True, raising=False)
    assert updates.install_type() == "windows"


# ------------------------------------------------------------- pages

def test_settings_card_and_button(client, github):
    html = client.get("/settings").get_data(as_text=True)
    assert f"Audible Watcher {__version__}" in html
    assert "Not checked yet" in html
    github["answers"].append(FakeResponse(data=release(bump())))
    r = client.post("/updates/check", follow_redirects=True)
    html = r.get_data(as_text=True)
    assert f"Audible Watcher {bump()[1:]} is available" in html
    assert "How to update" in html


def test_button_says_up_to_date(client, github):
    github["answers"].append(FakeResponse(data=release("v" + __version__)))
    html = client.post("/updates/check",
                       follow_redirects=True).get_data(as_text=True)
    assert "up to date" in html


def test_button_reports_a_failure(client, github):
    github["answers"].append(updates.requests.ConnectionError("x"))
    html = client.post("/updates/check",
                       follow_redirects=True).get_data(as_text=True)
    assert "Couldn&#39;t check for updates right now" in html


def test_dashboard_banner_and_dismiss(client, github):
    github["answers"] += [FakeResponse(data=release(bump())),
                          FakeResponse(data=release(bump(2)))]
    updates.check(force=True)
    assert "update-banner" in client.get("/").get_data(as_text=True)
    client.post("/updates/dismiss")
    assert "update-banner" not in client.get("/").get_data(as_text=True)
    updates.check(force=True)                       # an even newer one
    assert "update-banner" in client.get("/").get_data(as_text=True)


def test_no_banner_when_up_to_date(client, github):
    github["answers"].append(FakeResponse(data=release("v" + __version__)))
    updates.check(force=True)
    assert "update-banner" not in client.get("/").get_data(as_text=True)


def test_automatic_check_can_be_switched_off(client, github, monkeypatch):
    asked = []
    monkeypatch.setattr(updates, "check",
                        lambda force=False: asked.append(force))
    monkeypatch.setattr(webapp.checker, "check_all",
                        lambda trigger: {"aborted": False})
    webapp._check_lock.acquire()
    webapp._run_check("scheduled")
    assert asked == [False]                         # on by default
    client.post("/settings", data={"check_hour": "8", "smtp_port": "465"})
    assert db.get_settings()["update_check_auto"] == "0"
    webapp._check_lock.acquire()
    webapp._run_check("scheduled")
    assert asked == [False]                         # off: not asked again


def test_banner_hidden_when_automatic_check_is_off(client, github):
    github["answers"].append(FakeResponse(data=release(bump())))
    updates.check(force=True)
    db.save_settings({"update_check_auto": "0"})
    assert "update-banner" not in client.get("/").get_data(as_text=True)


def test_settings_checkboxes_save(client):
    html = client.get("/settings").get_data(as_text=True)
    assert 'name="update_check_auto"\n             checked' in html
    assert 'name="update_tray_item"\n             checked' in html
    client.post("/settings", data={"check_hour": "8", "smtp_port": "465",
                                   "update_check_auto": "on"})
    s = db.get_settings()
    assert s["update_check_auto"] == "1" and s["update_tray_item"] == "0"


# ------------------------------------------------------------- packaging

def test_release_workflow_checks_the_tag_against_version_py():
    wf = open(os.path.join(ROOT, ".github", "workflows", "release.yml"),
              encoding="utf-8").read()
    assert "from version import __version__" in wf
    assert re.search(r"needs: version", wf)
    assert "body_path: release-notes.md" in wf


def test_readme_lists_github_as_leaving_your_machine():
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    section = readme.split("What leaves your machine", 1)[1][:2500]
    assert "GitHub" in section and "update" in section.lower()
