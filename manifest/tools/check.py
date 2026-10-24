"""Tool 4, check_topic: does each migrated topic say the same thing as the DITA source?

For every build (unfiltered, plus one per DITAVAL file) the tool renders the *original* DITA
with DITA-OT HTML5 and the *migrated* MyST with Sphinx, then compares each published topic:

    pages      the same topics are published (and nothing extra)
    text       the authored text is the same, word for word, after normalising only rendering
               differences (whitespace, generated labels such as "Figure 1.", navigation)
    structure  the same number of headings, tables, rows, list items, code blocks, images and
               definition entries
    ids        every anchor DITA-OT publishes (topicid or topicid__elemid) exists in the Sphinx page

A topic passes only if every check passes in every build. Any difference is reported with the
words on each side, so a writer can see what was lost, added or changed.
"""

from __future__ import annotations

import difflib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lxml import html

from manifest.dita import load_map, parse
from manifest.tools.convert import find_dita
from manifest.tools.restore import NAV_CLASS, load_builds

BLOCK = {
    "p", "div", "li", "ul", "ol", "dl", "dt", "dd", "table", "thead", "tbody", "tfoot", "tr", "td", "th",
    "pre", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "figure", "figcaption", "caption",
    "br", "blockquote", "nav", "header", "footer", "main", "aside", "hr", "img",
}  # fmt: skip
STRUCTURE = {
    "headings": "self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::h6",
    "tables": "self::table",
    "table rows": "self::tr",
    "list items": "self::li",
    "code blocks": "self::pre",
    "images": "self::img",
    "definition terms": "self::dt",
}
DITA_ID = r"^{tid}(__[A-Za-z0-9_.:-]+?)?$"
MAX_DIFFS = 20


class CheckError(RuntimeError):
    pass


@dataclass
class Difference:
    check: str  # pages, text, structure, ids
    detail: str
    dita: str = ""
    myst: str = ""


@dataclass
class TopicBuildResult:
    build: str
    differences: list[Difference] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.differences


@dataclass
class TopicResult:
    topic: str
    builds: list[TopicBuildResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(b.ok for b in self.builds)

    def failed_checks(self) -> list[str]:
        return sorted({d.check for b in self.builds for d in b.differences})


@dataclass
class CheckResult:
    map: str
    builds: list[str]
    topics: list[TopicResult]
    page_differences: dict[str, list[Difference]]
    sphinx_warnings: dict[str, int]

    @property
    def ok(self) -> bool:
        return all(t.ok for t in self.topics) and not any(self.page_differences.values())

    def totals(self) -> dict[str, int]:
        return {
            "topics": len(self.topics),
            "topics_passed": sum(t.ok for t in self.topics),
            "builds": len(self.builds),
            "comparisons": sum(len(t.builds) for t in self.topics),
            "differences": sum(len(b.differences) for t in self.topics for b in t.builds),
            "page_differences": sum(len(v) for v in self.page_differences.values()),
        }

    def write(self, path: Path) -> None:
        d = asdict(self)
        d["ok"] = self.ok
        d["totals"] = self.totals()
        for td, t in zip(d["topics"], self.topics, strict=True):
            td["ok"] = t.ok
        path.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- rendering


def build_dita_html(dita: str, map_path: Path, ditaval: Path | None, out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    cmd = [dita, "-i", str(map_path), "-f", "html5", "-o", str(out)]
    if ditaval:
        cmd.append(f"--filter={ditaval}")
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise CheckError(f"DITA-OT html5 failed for {ditaval or 'unfiltered'}: {proc.stderr[-500:]}")


def build_sphinx_html(myst: Path, build: str, out: Path) -> int:
    if out.exists():
        shutil.rmtree(out)
    tags = [] if build == "unfiltered" else ["-t", "ditaval", "-t", build]
    proc = subprocess.run(
        [sys.executable, "-m", "sphinx", "-b", "html", "-q", *tags, str(myst), str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise CheckError(f"Sphinx failed for build {build}: {proc.stderr[-500:]}")
    return proc.stderr.count("WARNING")


# ---------------------------------------------------------------- extraction


def _content(page: Path, side: str) -> html.HtmlElement:
    root = html.parse(str(page)).getroot()
    found = root.xpath("//article") if side == "dita" else root.xpath('//div[@role="main"]')
    if not found:
        raise CheckError(f"no content area in {page}")
    el: html.HtmlElement = found[0]
    # Navigation and labels the toolchains generate are not authored text.
    drop = (
        ".//nav|.//*[contains(@class,'title-label')]"
        if side == "dita"
        else f".//*[contains(concat(' ',@class,' '),' {NAV_CLASS} ')]|.//*[contains(@class,'headerlink')]"
    )
    for bad in el.xpath(drop + "|.//script|.//style"):
        bad.drop_tree()
    return el


def text_of(el: html.HtmlElement) -> list[str]:
    """Words in reading order. Block elements separate words; inline elements don't, so
    `<span>v5e</span>,` reads 'v5e,' on both sides and highlighted code isn't split."""
    parts: list[str] = []

    def walk(node: html.HtmlElement) -> None:
        block = isinstance(node.tag, str) and node.tag in BLOCK
        if block:
            parts.append(" ")
        if node.text:
            parts.append(node.text)
        for child in node:
            if isinstance(child.tag, str):
                walk(child)
            if child.tail:
                parts.append(child.tail)
        if block:
            parts.append(" ")

    walk(el)
    return " ".join("".join(parts).replace(" ", " ").split()).split()


def structure_of(el: html.HtmlElement) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, test in STRUCTURE.items():
        nodes = el.xpath(f".//*[{test}]")
        if name == "table rows":
            nodes = [n for n in nodes if n.getparent() is not None]
        out[name] = len(nodes)
    return out


def ids_of(el: html.HtmlElement) -> set[str]:
    return {str(v) for v in el.xpath(".//@id")}


# ---------------------------------------------------------------- comparison


def compare_text(a: list[str], b: list[str]) -> list[Difference]:
    diffs: list[Difference] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        what = {"replace": "changed", "delete": "lost in migration", "insert": "added in migration"}[tag]
        ctx = " ".join(a[max(0, i1 - 6) : i1])
        diffs.append(Difference("text", f"{what} after '…{ctx}'", " ".join(a[i1:i2]), " ".join(b[j1:j2])))
    return diffs


def compare_topic(dita_page: Path, myst_page: Path, topic_id: str) -> list[Difference]:
    d, m = _content(dita_page, "dita"), _content(myst_page, "myst")
    diffs = compare_text(text_of(d), text_of(m))[:MAX_DIFFS]
    sd, sm = structure_of(d), structure_of(m)
    for k in STRUCTURE:
        if sd[k] != sm[k]:
            diffs.append(
                Difference("structure", f"{k}: DITA-OT {sd[k]}, Sphinx {sm[k]}", str(sd[k]), str(sm[k]))
            )
    pattern = re.compile(DITA_ID.format(tid=re.escape(topic_id)))
    wanted = {i for i in ids_of(d) if pattern.match(i) and "__entry__" not in i}
    missing = sorted(wanted - ids_of(html.parse(str(myst_page)).getroot()))
    if missing:
        diffs.append(
            Difference("ids", f"{len(missing)} anchor(s) missing: {', '.join(missing)}", ", ".join(missing))
        )
    return diffs


def check_topics(
    map_path: Path,
    myst_dir: Path,
    out_dir: Path,
    dita: str | None = None,
    ditavals: list[Path] | None = None,
    only_builds: list[str] | None = None,
    reuse_dita_html: bool = False,
) -> CheckResult:
    """only_builds limits the run (default: every build); reuse_dita_html skips DITA-OT when its
    output for a build is already in out_dir (the source didn't change)."""
    model = load_map(map_path)
    src = model.path.parent
    dita_cmd = find_dita(dita)
    if ditavals is None:
        ditavals = sorted(src.rglob("*.ditaval"))
    builds, _ = load_builds(ditavals)
    if only_builds is not None:
        builds = [b for b in builds if b.name in only_builds]
    by_name = {Path(p).stem: p for p in ditavals}

    published = [t for t in model.topics if t.processing_role != "resource-only"]
    results = {str(t.path.relative_to(src)): TopicResult(str(t.path.relative_to(src))) for t in published}
    ids = {str(t.path.relative_to(src)): parse(t.path).getroot().get("id") or "" for t in published}
    page_diffs: dict[str, list[Difference]] = {}
    warnings: dict[str, int] = {}

    for b in builds:
        d_out, s_out = out_dir / "dita-html" / b.name, out_dir / "sphinx-html" / b.name
        if not (reuse_dita_html and (d_out / "index.html").exists()):
            build_dita_html(dita_cmd, model.path, by_name.get(b.name) if b.ditaval else None, d_out)
        warnings[b.name] = build_sphinx_html(myst_dir, b.name, s_out)
        d_pages = {str(p.relative_to(d_out).with_suffix("")) for p in d_out.rglob("topics/**/*.html")}
        s_pages = {str(p.relative_to(s_out).with_suffix("")) for p in s_out.rglob("topics/**/*.html")}
        pd = [
            Difference("pages", f"published by DITA-OT, missing from Sphinx: {p}")
            for p in sorted(d_pages - s_pages)
        ]
        pd += [
            Difference("pages", f"published by Sphinx, not by DITA-OT: {p}")
            for p in sorted(s_pages - d_pages)
        ]
        page_diffs[b.name] = pd
        for rel, res in results.items():
            page = str(Path(rel).with_suffix(""))
            if page not in d_pages:
                continue  # not published in this build, by either side (page check covers the rest)
            tb = TopicBuildResult(b.name)
            if page not in s_pages:
                tb.differences.append(Difference("pages", "page missing from the Sphinx build"))
            else:
                tb.differences = compare_topic(d_out / f"{page}.html", s_out / f"{page}.html", ids[rel])
            res.builds.append(tb)

    result = CheckResult(
        model.path.name, [b.name for b in builds], list(results.values()), page_diffs, warnings
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    result.write(out_dir / "check.json")
    return result
