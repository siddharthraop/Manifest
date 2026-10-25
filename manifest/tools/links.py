"""Tool 5, check_links: no broken links, the same navigation as DITA-OT, and every old URL kept.

Runs on the HTML that check_topic built for every build (DITA-OT HTML5 of the source and Sphinx
of the migrated MyST), so it costs no extra builds:

    broken      lychee, offline, with fragment checking, over each Sphinx site
    navigation  each page's generated links (parent, child, related) go to the same pages as
                DITA-OT's <nav> for that page and build
    old URLs    every page DITA-OT published, and every anchor on it, resolves in the new site:
                at the same path, or through the redirect file this tool writes (US-05)

External links are listed, not fetched: the check runs offline so it is deterministic.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lxml import html

from manifest.tools.restore import NAV_CLASS

ANCHOR_IN_PAGE = re.compile(r'\bid="([^"]+)"')


class LinkError(RuntimeError):
    pass


@dataclass
class LinkProblem:
    build: str
    check: str  # broken, navigation, old-url
    page: str
    detail: str


@dataclass
class LinkResult:
    builds: list[str]
    lychee: str
    checked_links: int = 0
    external_links: int = 0
    old_urls: int = 0
    old_anchors: int = 0
    redirects: dict[str, str] = field(default_factory=dict)  # old page -> new page, where they differ
    problems: list[LinkProblem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def totals(self) -> dict[str, int]:
        by = {c: sum(1 for p in self.problems if p.check == c) for c in ("broken", "navigation", "old-url")}
        return {
            "checked_links": self.checked_links,
            "external_links_listed": self.external_links,
            "old_urls": self.old_urls,
            "old_anchors": self.old_anchors,
            "redirects": len(self.redirects),
            **{f"{k}_problems": v for k, v in by.items()},
        }

    def write(self, path: Path) -> None:
        d = asdict(self)
        d["ok"] = self.ok
        d["totals"] = self.totals()
        path.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")


def find_lychee(explicit: str | None = None) -> str:
    for c in (explicit, os.environ.get("LYCHEE"), shutil.which("lychee")):
        if c and Path(c).exists():
            return c
    raise LinkError("lychee not found. Install lychee and put it on PATH, set $LYCHEE, or pass --lychee.")


def lychee_version(lychee: str) -> str:
    out = subprocess.run([lychee, "--version"], capture_output=True, text=True, check=False).stdout
    return out.strip().split()[-1] if out.strip() else "unknown"


def run_lychee(lychee: str, site: Path) -> dict:  # type: ignore[type-arg]
    proc = subprocess.run(
        [lychee, "--offline", "--include-fragments", "--format", "json", "--no-progress", "."],
        cwd=site, capture_output=True, text=True, check=False,
    )  # fmt: skip
    try:
        data: dict = json.loads(proc.stdout)  # type: ignore[type-arg]
    except json.JSONDecodeError as exc:
        raise LinkError(f"lychee gave no JSON for {site}: {proc.stderr[-400:]}") from exc
    return data


def _nav_targets(page: Path, site: Path, side: str) -> set[str]:
    root = html.parse(str(page)).getroot()
    sel = (
        "//article//nav//a/@href"
        if side == "dita"
        else f"//*[contains(concat(' ',@class,' '),' {NAV_CLASS} ')]//a/@href"
    )
    here = posixpath.dirname(str(page.relative_to(site)))
    out = set()
    for href in root.xpath(sel):
        target = str(href).split("#")[0]
        if target and "://" not in target and not target.startswith("mailto:"):
            out.add(posixpath.normpath(posixpath.join(here, target)))
    return out


def redirect_stub(new_url: str) -> str:
    """A static redirect page that keeps the #anchor (GitHub Pages has no server redirects)."""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0; url={new_url}">'
        f'<link rel="canonical" href="{new_url}"><title>Moved</title>'
        f"<script>location.replace({json.dumps(new_url)} + location.hash)</script></head>"
        f'<body><p>This page moved to <a href="{new_url}">{new_url}</a>.</p></body></html>\n'
    )


def write_redirects(site: Path, redirects: dict[str, str]) -> None:
    for old, new in redirects.items():
        stub = site / old
        if stub.exists():
            raise LinkError(f"redirect would overwrite a real page: {old}")
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text(redirect_stub(os.path.relpath(site / new, stub.parent)), encoding="utf-8")


def check_links(
    check_dir: Path,
    out_dir: Path,
    lychee: str | None = None,
    redirects: dict[str, str] | None = None,
) -> LinkResult:
    """check_dir: the output folder of check_topic (dita-html/<build>, sphinx-html/<build>).
    redirects: old page path -> new page path, for pages the migration moved (default: none)."""
    cmd = find_lychee(lychee)
    builds = sorted(p.name for p in (check_dir / "sphinx-html").iterdir() if p.is_dir())
    if not builds:
        raise LinkError(f"no Sphinx builds in {check_dir}/sphinx-html; run check first")
    res = LinkResult(builds, lychee_version(cmd), redirects=dict(redirects or {}))

    # The anchors each page must keep: source IDs as DITA-OT published them, recorded by check_topic.
    expected: dict[tuple[str, str], list[str]] = {}
    check_json = check_dir / "check.json"
    if not check_json.exists():
        raise LinkError(f"{check_json} not found; run check first")
    for t in json.loads(check_json.read_text(encoding="utf-8"))["topics"]:
        page = str(Path(t["topic"]).with_suffix(".html"))
        for tb in t["builds"]:
            expected[(tb["build"], page)] = tb.get("anchors", [])

    for b in builds:
        dita_site, site = check_dir / "dita-html" / b, check_dir / "sphinx-html" / b
        write_redirects(site, res.redirects)

        data = run_lychee(cmd, site)
        res.checked_links += int(data.get("successful", 0)) + int(data.get("errors", 0))
        res.external_links += int(data.get("excludes", 0))
        for page, items in (data.get("error_map") or {}).items():
            for item in items:
                status = item.get("status", {})
                res.problems.append(
                    LinkProblem(
                        b,
                        "broken",
                        str(page).removeprefix("./"),
                        f"{item.get('url')}: {status.get('text', status)}",
                    )
                )

        dita_pages = sorted(str(p.relative_to(dita_site)) for p in dita_site.rglob("topics/**/*.html"))
        for page in dita_pages:
            new = res.redirects.get(page, page)
            target = site / new
            res.old_urls += 1
            if not target.exists():
                res.problems.append(LinkProblem(b, "old-url", page, "no page and no redirect for this URL"))
                continue
            dita_page = dita_site / page
            anchors = set(expected.get((b, page), []))
            have = set(ANCHOR_IN_PAGE.findall(target.read_text(encoding="utf-8")))
            res.old_anchors += len(anchors)
            for a in sorted(anchors - have):
                res.problems.append(LinkProblem(b, "old-url", page, f"anchor #{a} does not resolve"))
            if page in res.redirects:
                continue
            want, got = _nav_targets(dita_page, dita_site, "dita"), _nav_targets(target, site, "myst")
            for t in sorted(want - got):
                res.problems.append(
                    LinkProblem(b, "navigation", page, f"DITA-OT links to {t}; the new page doesn't")
                )
            for t in sorted(got - want):
                res.problems.append(
                    LinkProblem(b, "navigation", page, f"the new page links to {t}; DITA-OT doesn't")
                )

    out_dir.mkdir(parents=True, exist_ok=True)
    note = "every other DITA-OT URL keeps its path"
    (out_dir / "redirects.json").write_text(
        json.dumps({"redirects": res.redirects, "unchanged": note}, indent=2) + "\n", encoding="utf-8"
    )
    res.write(out_dir / "links.json")
    return res
