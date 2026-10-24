"""Tool 2, convert_map: DITA-OT 4.4 'markdown' build of a working copy full of placeholders.

The source is never changed. The tool:

1. copies the map's folder to a working directory;
2. replaces keys, conrefs, conditions and droppable IDs in each published topic with tokens;
3. builds one include topic per distinct conref target, from the original source element, so
   shared content is converted once and later restored as one include file;
4. runs DITA-OT twice (the map, then the includes) with the standard `markdown` format;
5. proves the placeholders survived: every token in the ledger must appear exactly once in the
   Markdown file it belongs to. A missing or repeated token fails that file.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree

from manifest.dita import find_by_id, load_map, parse, split_href
from manifest.placeholders import TOKEN, Entry, Ledger, tokenize
from manifest.rules import RuleSet

INCLUDES_DIR = "_manifest_includes"
DITA_OT_FORMAT = "markdown"  # not markdown_github, which drops more (design doc §5.2)

DOCTYPES = {
    "task": '<!DOCTYPE task PUBLIC "-//OASIS//DTD DITA Task//EN" "task.dtd">',
    "topic": '<!DOCTYPE topic PUBLIC "-//OASIS//DTD DITA Topic//EN" "topic.dtd">',
    "map": '<!DOCTYPE map PUBLIC "-//OASIS//DTD DITA Map//EN" "map.dtd">',
}
STEP_LIKE = {"step", "substep"}
INLINE = {"ph", "keyword", "term", "codeph", "varname", "b", "i", "u", "xref", "q"}


class ConvertError(RuntimeError):
    pass


@dataclass
class FileCheck:
    owner: str
    markdown: str
    expected: int
    missing: list[str] = field(default_factory=list)
    repeated: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    via_link_text: list[str] = field(
        default_factory=list
    )  # title/shortdesc keys copied into link text: allowed

    @property
    def ok(self) -> bool:
        return not (self.missing or self.repeated or self.unexpected)


@dataclass
class ConvertResult:
    map: str
    out_dir: str
    dita_ot: str
    files: list[FileCheck]
    includes: dict[str, str]  # include id -> conref target (normalised)
    ledger: list[dict[str, Any]]
    rules_applied: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(f.ok for f in self.files)

    @property
    def unhandled(self) -> list[dict[str, Any]]:
        return [e for e in self.ledger if e["kind"] == "unhandled"]

    def totals(self) -> dict[str, int]:
        kinds = Counter(e["kind"] for e in self.ledger)
        return {
            "files": len(self.files),
            "files_ok": sum(f.ok for f in self.files),
            "tokens": sum(f.expected for f in self.files),
            "missing": sum(len(f.missing) for f in self.files),
            "repeated": sum(len(f.repeated) for f in self.files),
            "includes": len(self.includes),
            **{k: v for k, v in sorted(kinds.items())},
        }

    def write(self, path: Path) -> None:
        d = asdict(self)
        d["ok"] = self.ok
        d["totals"] = self.totals()
        path.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")


def find_dita(explicit: str | None = None) -> str:
    """The DITA-OT launcher: --dita, then $DITA_HOME/bin/dita, then `dita` on PATH."""
    candidates = [explicit] if explicit else []
    if os.environ.get("DITA_HOME"):
        candidates.append(str(Path(os.environ["DITA_HOME"]) / "bin" / "dita"))
    found = shutil.which("dita")
    if found:
        candidates.append(found)
    for c in candidates:
        if c and Path(c).exists():
            return c
    raise ConvertError("DITA-OT not found. Install DITA-OT 4.4 and set DITA_HOME, or pass --dita.")


def dita_ot_version(dita: str) -> str:
    out = subprocess.run([dita, "--version"], capture_output=True, text=True, check=False).stdout
    m = re.search(r"DITA-OT version (\S+)", out)
    return m.group(1) if m else "unknown"


def _write(tree: etree._ElementTree, path: Path, doctype: str | None = None) -> None:
    """Write a working-copy topic. `doctype`: the source's, when the tree came from a rule (an XSLT
    result has none, and DITA-OT needs it to give each element its DITA class)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # lxml supports DocInfo.doctype and write(doctype=); lxml-stubs 0.5 don't declare them.
    doctype = doctype or tree.docinfo.doctype or None  # type: ignore[attr-defined]
    tree.write(str(path), xml_declaration=True, encoding="UTF-8", doctype=doctype)  # type: ignore[call-arg]


def _wrap_include(inc_id: str, el: etree._Element) -> tuple[str, etree._Element]:
    """Put a copied conref target in the smallest valid topic DITA-OT will render."""
    tag = str(el.tag)
    if tag in STEP_LIKE:
        root = etree.Element("task", id=inc_id)
        etree.SubElement(root, "title").text = inc_id
        steps = etree.SubElement(etree.SubElement(root, "taskbody"), "steps")
        el.tag = "step"
        steps.append(el)
        return "task", root
    root = etree.Element("topic", id=inc_id)
    etree.SubElement(root, "title").text = inc_id
    body = etree.SubElement(root, "body")
    if tag in INLINE:
        etree.SubElement(body, "p").append(el)
    else:
        body.append(el)
    el.tail = None
    return "topic", root


def _run_dita(dita: str, inp: Path, out: Path, log: Path) -> None:
    proc = subprocess.run(
        [dita, "-i", str(inp), "-f", DITA_OT_FORMAT, "-o", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    log.write_text(proc.stdout + proc.stderr, encoding="utf-8")
    if proc.returncode != 0:
        raise ConvertError(f"DITA-OT failed on {inp.name} (exit {proc.returncode}); see {log}")


def convert_map(
    map_path: Path, out_dir: Path, dita: str | None = None, rules: RuleSet | None = None
) -> ConvertResult:
    """rules: approved mapping rules, applied to each topic in the working copy before tokenizing."""
    model = load_map(map_path)
    src = model.path.parent
    dita_cmd = find_dita(dita)
    work = out_dir / "work"
    md_out = out_dir / "markdown"
    for d in (work, md_out):
        if d.exists():
            shutil.rmtree(d)
    shutil.copytree(src, work, ignore=shutil.ignore_patterns(".git", "build", "out"))

    ledger = Ledger()
    applied: list[dict[str, Any]] = []  # which rule rewrote how many elements in which topic
    published: dict[str, str] = {}  # owner -> markdown path (relative to md_out)

    # 1. Published topics: tokenize the working copy in place.
    for t in model.topics:
        rel = str(t.path.relative_to(src))
        if t.processing_role == "resource-only":
            continue
        tree = parse(t.path)
        doctype = tree.docinfo.doctype  # type: ignore[attr-defined]
        if rules is not None:
            for rule_id, n in rules.matches(tree.getroot()).items():
                applied.append({"rule": rule_id, "topic": rel, "elements": n})
            tree = rules.apply(tree)
        tokenize(tree.getroot(), ledger, rel)
        _write(tree, work / rel, doctype)
        published[rel] = str(Path(rel).with_suffix(".md"))

    # 2. One include per distinct conref target, built from the original source element.
    includes: dict[str, str] = {}
    by_target: dict[str, str] = {}
    for e in list(ledger.entries):
        if e.kind != "conref":
            continue
        target = str(e.detail["target"])
        f, tid, eid = split_href(target)
        target_path = (src / Path(e.owner).parent / f).resolve() if f else (src / e.owner).resolve()
        norm = f"{target_path.relative_to(src)}#{tid or ''}/{eid or ''}"
        e.detail["normalised"] = norm
        if norm in by_target:
            e.detail["include"] = by_target[norm]
            continue
        el = find_by_id(target_path, tid, eid, rules)
        if el is None:
            ledger.entries.append(
                Entry(
                    "",
                    "unhandled",
                    e.owner,
                    e.element,
                    e.line,
                    {"reason": f"conref target not found: {target}"},
                )
            )
            continue
        inc_id = f"inc-{len(includes) + 1}"
        by_target[norm] = inc_id
        e.detail["include"] = inc_id
        includes[inc_id] = norm
        copy = deepcopy(el)
        copy.tail = None
        kind, root = _wrap_include(inc_id, copy)
        owner = f"{INCLUDES_DIR}/{inc_id}.dita"
        tokenize(root, ledger, owner)
        tree = etree.ElementTree(root)
        path = work / owner
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(etree.tostring(tree, xml_declaration=True, encoding="UTF-8", doctype=DOCTYPES[kind]))
        published[owner] = str(Path(owner).with_suffix(".md"))

    # 3. Run DITA-OT: the map, then a map of the includes (keys resolve the same way).
    _run_dita(dita_cmd, work / model.path.name, md_out, out_dir / "dita-ot-map.log")
    if includes:
        inc_map = work / INCLUDES_DIR / "includes.ditamap"
        refs = "".join(f'<topicref href="{i}.dita"/>' for i in includes)
        keys = "".join(
            f'<mapref href="../{c.get("href")}" processing-role="resource-only"/>'
            for c in parse(model.path).getroot().iter("mapref")
            if c.get("type") != "subjectScheme"
        )
        inc_map.write_text(
            f'<?xml version="1.0" encoding="UTF-8"?>\n{DOCTYPES["map"]}\n'
            f"<map><title>MANIFEST includes</title>{keys}{refs}</map>\n",
            encoding="utf-8",
        )
        _run_dita(dita_cmd, inc_map, md_out / INCLUDES_DIR, out_dir / "dita-ot-includes.log")

    # 4. Prove every token survived exactly once, in the right file.
    expected = ledger.expected()
    title_keys = {
        e.token for e in ledger.entries if e.kind == "key" and e.detail.get("in_link_text") == "yes"
    }
    checks: list[FileCheck] = []
    for owner, md_rel in published.items():
        md_path = md_out / md_rel
        if owner.startswith(INCLUDES_DIR):
            md_path = md_out / INCLUDES_DIR / Path(md_rel).name
        text = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        seen = Counter(m.group(0) for m in TOKEN.finditer(text))
        want = expected.get(owner, [])
        fc = FileCheck(owner, str(md_path.relative_to(out_dir)), len(want))
        fc.missing = [t for t in want if seen[t] == 0]
        fc.repeated = [t for t in want if seen[t] > 1]
        extra = set(seen) - set(want)
        fc.via_link_text = sorted(extra & title_keys)
        fc.unexpected = sorted(extra - title_keys)
        if not md_path.exists():
            fc.missing = list(want) or ["<no markdown written>"]
        checks.append(fc)

    ledger_dicts = [asdict(e) for e in ledger.entries]
    result = ConvertResult(
        model.path.name, str(out_dir), dita_ot_version(dita_cmd), checks, includes, ledger_dicts, applied
    )
    (out_dir / "ledger.json").write_text(json.dumps(ledger_dicts, indent=2) + "\n", encoding="utf-8")
    result.write(out_dir / "convert.json")
    return result
