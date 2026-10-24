"""US-05 · Technical Support Engineer: every old URL and anchor opens the right page or redirects.

Accepted when: each element ID is kept as an anchor · a redirect file is created for each map ·
links that cannot be resolved are listed by name.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from conftest import load, needs_toolchain

from manifest.tools.links import check_links, redirect_stub


@needs_toolchain
def test_every_old_url_and_anchor_resolves(migrated: tuple[dict[str, Any], Path]) -> None:
    _, out = migrated
    links = load(out / "links" / "links.json")
    assert links["ok"] and links["totals"]["old_urls"] == 114
    # Every anchor DITA-OT derived from a source ID, in every build, was checked (and resolved).
    check = load(out / "check" / "check.json")
    recorded = sum(len(b["anchors"]) for t in check["topics"] for b in t["builds"])
    assert links["totals"]["old_anchors"] == recorded > 100
    assert links["totals"]["broken_problems"] == links["totals"]["navigation_problems"] == 0


@needs_toolchain
def test_a_redirect_file_is_created_for_the_map(migrated: tuple[dict[str, Any], Path]) -> None:
    _, out = migrated
    data = load(out / "links" / "redirects.json")
    assert data["redirects"] == {}  # Sphinx keeps DITA-OT's paths, so nothing needs to move
    assert "keeps its path" in data["unchanged"]


def _copy_check(out: Path, tmp: Path) -> Path:
    dst = tmp / "check"
    for side in ("dita-html", "sphinx-html"):
        shutil.copytree(out / "check" / side / "unfiltered", dst / side / "unfiltered")
    shutil.copy(out / "check" / "check.json", dst / "check.json")  # the anchors each page must keep
    return dst


@needs_toolchain
def test_unresolvable_old_urls_are_listed_by_name(
    migrated: tuple[dict[str, Any], Path], tmp_path: Path
) -> None:
    _, out = migrated
    check = _copy_check(out, tmp_path)
    (check / "sphinx-html" / "unfiltered" / "topics" / "tpu-networking.html").unlink()
    res = check_links(check, tmp_path / "links")
    problems = {(p.check, p.page, p.detail) for p in res.problems}
    assert ("old-url", "topics/tpu-networking.html", "no page and no redirect for this URL") in problems
    assert any(p.check == "broken" for p in res.problems), "links to the missing page are broken too"


@needs_toolchain
def test_a_moved_page_resolves_through_its_redirect(
    migrated: tuple[dict[str, Any], Path], tmp_path: Path
) -> None:
    """If a page moves, the redirect stub keeps the old URL and its #anchor working."""
    _, out = migrated
    check = _copy_check(out, tmp_path)
    site = check / "sphinx-html" / "unfiltered" / "topics"
    (site / "networking").mkdir()
    (site / "tpu-networking.html").rename(site / "networking" / "tpu-networking.html")
    for page in site.glob("*.html"):  # update links in the new site, as a real move would
        page.write_text(page.read_text().replace("tpu-networking.html", "networking/tpu-networking.html"))
    res = check_links(
        check,
        tmp_path / "links",
        redirects={"topics/tpu-networking.html": "topics/networking/tpu-networking.html"},
    )
    assert not [p for p in res.problems if p.check == "old-url"], res.problems
    stub = (site / "tpu-networking.html").read_text()
    assert "networking/tpu-networking.html" in stub and "location.hash" in stub


def test_redirect_stub_keeps_the_anchor() -> None:
    page = redirect_stub("new/page.html")
    assert 'http-equiv="refresh"' in page and 'location.replace("new/page.html" + location.hash)' in page
