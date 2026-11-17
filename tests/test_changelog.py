"""CHANGELOG.md is the source of the release notes: every release needs a
section in it (the release workflow stops otherwise)."""
import os
import re
import subprocess
import sys

import pytest

from version import __version__

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packaging"))
import release_notes  # noqa: E402

TEXT = open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8").read()


def test_the_current_version_has_notes():
    notes = release_notes.section(__version__)
    assert notes and len(notes.splitlines()) >= 3
    assert release_notes.section("v" + __version__) == notes


def test_newest_version_comes_first_and_is_the_current_one():
    versions = re.findall(r"^## (\d+\.\d+\.\d+) — \d{4}-\d{2}-\d{2}$",
                          TEXT, re.MULTILINE)
    assert versions[0] == __version__
    keyed = [tuple(int(p) for p in v.split(".")) for v in versions]
    assert keyed == sorted(keyed, reverse=True)       # newest first
    assert len(set(versions)) == len(versions)        # no duplicates
    assert "1.0.0" in versions                        # history is complete


def test_every_version_heading_has_a_date():
    for line in TEXT.splitlines():
        if line.startswith("## "):
            assert re.fullmatch(r"## \d+\.\d+\.\d+ — \d{4}-\d{2}-\d{2}", line)


def test_a_section_stops_at_the_next_version():
    text = ("# Changelog\n\n## 1.1.0 — 2026-01-02\n\nNew.\n\n### Part\n"
            "- a\n\n## 1.0.0 — 2026-01-01\n\nOld.\n")
    assert release_notes.section("1.1.0", text) == "New.\n\n### Part\n- a"
    assert release_notes.section("1.0.0", text) == "Old."
    assert release_notes.section("1.0", text) is None   # whole number only
    assert release_notes.section("2.0.0", text) is None


def test_missing_version_fails_the_script():
    script = os.path.join(ROOT, "packaging", "release_notes.py")
    bad = subprocess.run([sys.executable, script, "v99.0.0"],
                         capture_output=True, text=True)
    assert bad.returncode == 1 and "no section" in bad.stderr
    good = subprocess.run([sys.executable, script, "v" + __version__],
                          capture_output=True, encoding="utf-8")
    assert good.returncode == 0
    assert good.stdout.strip() == release_notes.section(__version__)


def test_release_workflow_takes_notes_from_the_changelog():
    wf = open(os.path.join(ROOT, ".github", "workflows", "release.yml"),
              encoding="utf-8").read()
    assert wf.count("packaging/release_notes.py") == 2  # check + notes
    assert "git log" not in wf


@pytest.mark.parametrize("where", ["README.md"])
def test_changelog_is_linked(where):
    assert "CHANGELOG.md" in open(os.path.join(ROOT, where),
                                  encoding="utf-8").read()


def test_settings_links_the_changelog(client):
    html = client.get("/settings").get_data(as_text=True)
    assert "/blob/main/CHANGELOG.md" in html
