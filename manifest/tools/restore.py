"""Tool 3, restore_reuse: turn the placeholders back into MyST Markdown for Sphinx.

Works from convert_map's ledger, never by guessing from text. Every token is mapped:

    key        -> {{ substitution }}; {{name}} inside code (sphinx-substitution-extensions)
    conref     -> an include file written once in _includes/; an inline conref -> a substitution
    condition  -> a Sphinx `only` block; a condition on a list item or table row is lifted to the
                  whole list or table, one variant per build, so numbering and rows stay right
    id         -> an anchor in DITA-OT HTML5's own form, topicid__elemid, so old URLs resolve

Conditions are evaluated with real DITAVAL semantics for every build: the unfiltered build plus
one build per DITAVAL file. Each filtered build sets the Sphinx tags `ditaval` and its file
stem, so `{only} v5e or not ditaval` means "in the v5e build and in the unfiltered build".

Everything the tool cannot map is recorded with a reason. Leftover tokens are a hard error.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token

from manifest.dita import PROFILE_ATTRS, load_map, parse
from manifest.placeholders import TOKEN
from manifest.tools.convert import INCLUDES_DIR

INLINE_CONREF_ELEMENTS = {"ph", "keyword", "term", "codeph", "q", "cite", "varname", "text"}
LIST_ITEM_ELEMENTS = {"step", "substep", "li", "sli", "choice"}
TABLE_ROW_ELEMENTS = {"row", "strow", "sthead"}
HEADING_ATTRS = re.compile(r"^(#{1,6}) (.*?) \{#([^\s}]+)((?: \.[\w-]+)*)\}\s*$")
HEADING_CLASSES = re.compile(r"^(#{1,6}) (.*?) \{(?: \.[\w-]+)+\}\s*$")  # `{ .section}`: no ID
FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})(.*)$")
INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")
LIST_MARKER = re.compile(r"^(\s*)(\d+\.|[-*+])(\s+)")
NOTE_LABEL = re.compile(r"^\*\*[A-Z][A-Za-z ]*:\*\* ")
ALL = "unfiltered"


class RestoreError(RuntimeError):
    pass


# ---------------------------------------------------------------- DITAVAL builds


@dataclass(frozen=True)
class Build:
    name: str  # "unfiltered" or the DITAVAL file stem, sanitised as a Sphinx tag
    ditaval: str | None
    include: dict[str, frozenset[str]]  # attribute -> values explicitly included
    exclude: dict[str, frozenset[str]]  # attribute -> values explicitly excluded
    default_exclude: frozenset[str]  # attributes whose other values are excluded

    def visible(self, attr: str, values: list[str]) -> bool:
        """DITAVAL: an element is excluded only if every one of its values is excluded."""
        if self.ditaval is None:
            return True

        def excluded(v: str) -> bool:
            if v in self.include.get(attr, frozenset()):
                return False
            return v in self.exclude.get(attr, frozenset()) or attr in self.default_exclude

        return not all(excluded(v) for v in values)


def tag(name: str) -> str:
    t = re.sub(r"\W", "_", name)
    return t if t and not t[0].isdigit() else f"t_{t}"


def load_builds(ditavals: list[Path]) -> tuple[list[Build], list[str]]:
    builds = [Build(ALL, None, {}, {}, frozenset())]
    problems: list[str] = []
    for path in sorted(ditavals):
        inc: dict[str, set[str]] = {}
        exc: dict[str, set[str]] = {}
        default: set[str] = set()
        for prop in parse(path).getroot().iter("prop"):
            att, val, action = prop.get("att"), prop.get("val"), prop.get("action")
            if att is None or action not in ("include", "exclude"):
                problems.append(
                    f"{path.name}: prop {att!r} action {action!r} has no mapping (only include/exclude)"
                )
                continue
            if val is None:
                if action == "exclude":
                    default.add(att)
                continue
            (inc if action == "include" else exc).setdefault(att, set()).add(val)
        builds.append(
            Build(
                tag(path.stem),
                path.name,
                {k: frozenset(v) for k, v in inc.items()},
                {k: frozenset(v) for k, v in exc.items()},
                frozenset(default),
            )
        )
    return builds, problems


def _all_visible(build: Build, conditions: dict[str, tuple[str, ...]]) -> bool:
    return all(build.visible(a, list(v)) for a, v in conditions.items() if a in PROFILE_ATTRS)


def only_expression(visible_in: list[Build], builds: list[Build]) -> str | None:
    """Sphinx `only` expression for 'visible in exactly these builds', or None if in all."""
    names = {b.name for b in visible_in}
    if names == {b.name for b in builds}:
        return None
    parts = [b.name for b in builds if b.name in names and b.name != ALL]
    if ALL in names:
        parts.append("not ditaval")
    return " or ".join(parts) if parts else "ditaval and not ditaval"


# ---------------------------------------------------------------- results


@dataclass
class Mapping:
    token: str
    kind: str
    owner: str
    became: str  # what the token became, e.g. "{{ tpu7x_chips_per_pod }}"
    note: str = ""


@dataclass
class RestoreResult:
    out_dir: str
    builds: list[dict[str, Any]]
    mappings: list[Mapping] = field(default_factory=list)
    unhandled: list[dict[str, Any]] = field(default_factory=list)
    substitutions: dict[str, str] = field(default_factory=dict)
    includes: dict[str, str] = field(default_factory=dict)  # include path -> conref target
    findings: list[dict[str, Any]] = field(default_factory=list)  # source issues found on the way

    def totals(self) -> dict[str, int]:
        def n(kind: str, prefix: str = "") -> int:
            """Distinct source tokens: a lifted list or table repeats its tokens in each variant."""
            return len({m.token for m in self.mappings if m.kind == kind and m.became.startswith(prefix)})

        return {
            "keys": n("key"),
            "conrefs_as_includes": n("conref", "```{include}"),
            "conrefs_as_substitutions": n("conref", "{{"),
            "conditions": n("condition"),
            "ids": n("id", "<span id="),
            "ids_dropped": n("id", "dropped"),
            "substitutions_defined": len(self.substitutions),
            "include_files": len(self.includes),
            "unhandled": len(self.unhandled),
            "occurrences": len(self.mappings),
        }

    def write(self, path: Path) -> None:
        d = asdict(self)
        d["totals"] = self.totals()
        path.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- Markdown structure


_MD = MarkdownIt("commonmark").enable("table")


def _blocks(lines: list[str]) -> list[Token]:
    return [t for t in _MD.parse("\n".join(lines)) if t.map is not None and t.nesting >= 0]


def _innermost(tokens: list[Token], line: int, types: set[str]) -> Token | None:
    """The smallest block of one of these types that contains the line."""
    hits = [t for t in tokens if t.type in types and t.map and t.map[0] <= line < t.map[1]]
    return min(hits, key=lambda t: t.map[1] - t.map[0] if t.map else 0, default=None)


def _line_of(lines: list[str], token: str) -> int:
    for i, ln in enumerate(lines):
        if token in ln:
            return i
    raise RestoreError(f"token {token} not found")


@dataclass
class _Edit:
    start: int
    end: int
    new: list[str]


def _strip(lines: list[str], tokens: set[str]) -> list[str]:
    out = []
    for ln in lines:
        for t in tokens:
            ln = ln.replace(t, "")
        out.append(ln)
    return out


def _renumber(item_lines: list[list[str]]) -> list[str]:
    out: list[str] = []
    for n, item in enumerate(item_lines, 1):
        first, *rest = item
        m = LIST_MARKER.match(first)
        if m and m.group(2)[0].isdigit():
            width = len(m.group(2)) + len(m.group(3))
            marker = f"{n}."
            first = m.group(1) + marker + " " * max(1, width - len(marker)) + first[m.end() :]
        out.extend([first, *rest])
    return out


def _fenced(indent: str, expr: str, body: list[str]) -> list[str]:
    return [f"{indent}::::{{only}} {expr}", *body, f"{indent}::::", ""]


def apply_conditions(
    lines: list[str], conds: list[dict[str, Any]], builds: list[Build], result: RestoreResult, owner: str
) -> list[str]:
    """Rewrite condition tokens as `only` blocks. conds: ledger entries of kind 'condition'."""
    if not conds:
        return lines
    toks = _blocks(lines)
    groups: dict[tuple[str, int, int], list[tuple[int, int, dict[str, Any]]]] = {}
    edits: list[_Edit] = []
    all_tokens = {c["token"] for c in conds} | {c["token"].replace("@@B", "@@E") for c in conds}

    for c in conds:
        b = _line_of(lines, c["token"])
        e = _line_of(lines, c["token"].replace("@@B", "@@E"))
        attr, values = c["detail"]["attribute"], list(c["detail"]["values"])
        visible = [bd for bd in builds if bd.visible(attr, values)]
        expr = only_expression(visible, builds)
        if c["element"] in TABLE_ROW_ELEMENTS:
            table = _innermost(toks, b, {"table_open"})
            if table is None or table.map is None:
                raise RestoreError(f"{owner}: row condition {c['token']} is not in a table")
            groups.setdefault(("table", table.map[0], table.map[1]), []).append((b, b + 1, c))
        elif c["element"] in LIST_ITEM_ELEMENTS:
            item = _innermost(toks, b, {"list_item_open"})
            lst = _innermost(toks, b, {"ordered_list_open", "bullet_list_open"})
            if item is None or lst is None or item.map is None or lst.map is None:
                raise RestoreError(f"{owner}: list condition {c['token']} is not in a list item")
            groups.setdefault(("list", lst.map[0], lst.map[1]), []).append((item.map[0], item.map[1], c))
        else:
            block = _innermost(toks, b, {"paragraph_open", "fence", "table_open", "blockquote_open"})
            if block is None or block.map is None or not (block.map[0] <= e < block.map[1]):
                raise RestoreError(f"{owner}: condition {c['token']} does not sit in one block")
            s, end = block.map
            indent = re.match(r"\s*", lines[s]).group(0)  # type: ignore[union-attr]
            body = _strip(lines[s:end], all_tokens)
            edits.append(_Edit(s, end, _fenced(indent, expr, body) if expr else body))
        result.mappings.append(
            Mapping(
                c["token"],
                "condition",
                owner,
                f"{{only}} {expr}" if expr else "(visible in every build)",
                f"@{attr}={' '.join(values)} on <{c['element']}>",
            )
        )

    for (kind, s, end), members in groups.items():
        # Units: table rows or list items, in order; each unit visible per build.
        if kind == "table":
            header = lines[s : s + 2]
            units = [(i, i + 1) for i in range(s + 2, end) if lines[i].strip()]
        else:
            items = [
                t
                for t in toks
                if t.type == "list_item_open"
                and t.map
                and s <= t.map[0] < end
                and t.level == (_innermost(toks, s, {"ordered_list_open", "bullet_list_open"}) or t).level + 1
            ]
            units = [(t.map[0], t.map[1]) for t in items if t.map]
            header = []
        conds_by_unit: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for us, _ue, c in members:
            unit = next(u for u in units if u[0] <= us < u[1])
            conds_by_unit.setdefault(unit, []).append(c)
        variants: dict[tuple[tuple[int, int], ...], list[Build]] = {}
        for bd in builds:
            shown = tuple(
                u
                for u in units
                if all(
                    bd.visible(c["detail"]["attribute"], list(c["detail"]["values"]))
                    for c in conds_by_unit.get(u, [])
                )
            )
            variants.setdefault(shown, []).append(bd)
        indent = re.match(r"\s*", lines[s]).group(0)  # type: ignore[union-attr]
        new: list[str] = []
        for shown, in_builds in variants.items():
            if kind == "table":
                body = header + [lines[u[0]] for u in shown]
            else:
                body = _renumber([lines[u[0] : u[1]] for u in shown])
            body = _strip(body, all_tokens)
            while body and not body[-1].strip():
                body.pop()
            expr = only_expression(in_builds, builds)
            new.extend(_fenced(indent, expr, body) if expr else body + [""])
        last = units[-1][1] if units else end
        edits.append(_Edit(s, max(end, last), new))

    for ed in sorted(edits, key=lambda x: x.start, reverse=True):
        lines[ed.start : ed.end] = ed.new
    leftovers = [t for t in all_tokens if any(t in ln for ln in lines)]
    if leftovers:
        raise RestoreError(f"{owner}: condition tokens left after restore: {leftovers}")
    return lines


LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+\.md)(#[^)]*)?\)")
SOLE_LINK = re.compile(r"^\s*(?:[-*+]\s+)?(?:\*\*[^*\[]*:\*\*\s*)?(\*\*)?(\[[^\]]*\]\([^)\s]+\))\1?\s*$")
GUARD_OPEN = "% manifest:published-in "
GUARD_CLOSE = "% manifest:end"


def guard_links(
    lines: list[str],
    md_rel: str,
    link_builds: Callable[[str], list[str]],
    builds: list[Build],
    by_build: dict[str, Build],
    result: RestoreResult,
    owner: str,
) -> list[str]:
    """Links DITA-OT generates (parent, child and related links) exist only in the builds where
    their relationship holds (`link_builds`, from the map): DITA-OT leaves them out elsewhere.
    They are wrapped in guard comments that conf.py's source-read hook removes before parsing,
    because Sphinx resolves links even inside an excluded `only` block and warns (design doc
    §5.4). A link in running text can't be cut that way; it is recorded for review instead."""
    here = Path(md_rel).parent
    toks = _blocks(lines)
    edits: list[_Edit] = []
    done: set[int] = set()
    for i, line in enumerate(lines):
        targets = [os.path.normpath(here / m.group(2)) for m in LINK.finditer(line)]
        limited = [p for p in targets if len(link_builds(p)) != len(builds)]
        if not limited or i in done:
            continue
        if len(targets) == 1 and SOLE_LINK.match(line):
            item = _innermost(toks, i, {"list_item_open"})
            s, e = (item.map[0], item.map[1]) if item and item.map and item.map[0] == i else (i, i + 1)
            while e > s + 1 and not lines[e - 1].strip():
                e -= 1
            done.update(range(s, e))
            builds_in = link_builds(limited[0])
            if len(builds_in) == len(builds):
                continue
            edits.append(_Edit(s, e, [GUARD_OPEN + ",".join(builds_in), *lines[s:e], GUARD_CLOSE]))
            result.mappings.append(
                Mapping("", "link", owner, f"published in {builds_in}", f"link to {limited[0]}")
            )
        else:
            for p in limited:
                result.unhandled.append(
                    {
                        "owner": owner,
                        "token": "",
                        "reason": f"link in running text to {p}, which some builds don't have",
                    }
                )
    for ed in sorted(edits, key=lambda x: x.start, reverse=True):
        lines[ed.start : ed.end] = ed.new
    return lines


def _possible_typed_copies(model: Any, src: Path) -> list[dict[str, Any]]:
    """Text in the DITA source that equals a key's value (3+ characters with a digit) but isn't a
    key reference. The migration keeps it as typed; a writer decides whether it should be the key."""
    by_value: dict[str, list[str]] = {}
    for k, v in model.keys.items():
        if len(v) >= 3 and re.search(r"\d", v):
            by_value.setdefault(v, []).append(k)
    out: list[dict[str, Any]] = []
    for t in model.topics:
        root = parse(t.path).getroot()
        for el in root.iter():
            if not isinstance(el.tag, str) or el.get("keyref") or str(el.tag) in ("prolog", "topicmeta"):
                continue
            if any(a.get("keyref") or str(a.tag) in ("prolog", "topicmeta") for a in el.iterancestors()):
                continue
            for text in (el.text, *(c.tail for c in el)):
                for value, keys in by_value.items():
                    if text and re.search(rf"(?<![\w.-]){re.escape(value)}(?![\w.-])", text):
                        out.append(
                            {
                                "kind": "possible-typed-copy",
                                "topic": str(t.path.relative_to(src)),
                                "line": el.sourceline,
                                "value": value,
                                "keys": sorted(keys),
                                "detail": f"'{value}' is typed here; it is also the value of "
                                f"{', '.join(sorted(keys))}. Use the key if it means the same thing.",
                            }
                        )
    return out


IMAGE_LINE = re.compile(r'^\s*(?:<span id="[^"]*"></span>)?!\[[^\]]*\]\(([^)\s]+)(?:\s+"([^"]*)")?\)\s*$')
CHILD_LINK = re.compile(r"^-\s+\*\*\[[^\]]*\]\(([^)\s]+\.md)\)\*\*\s*$")
NAV_CLASS = "manifest-nav"
FIGURE_CLASS = "manifest-figure"


def _wrap(css: str, body: list[str], lead: list[str] | None = None) -> list[str]:
    # Five colons, so `only` blocks (four) can nest inside.
    return ["", f":::::{{container}} {css}", "", *(lead or []), *body, "", ":::::", ""]


HTML_BLOCK_GLUE = re.compile(r"^(\s*)(.*</(?:table|div|pre|dl)>)(\S.*)$")


def unglue_html_blocks(lines: list[str]) -> list[str]:
    """DITA-OT's Markdown writer falls back to a raw HTML table when a cell holds several
    paragraphs, then writes the next block on the same line: `</table>1.  Find the…`. Markdown
    then reads that block as part of the HTML, swallowing a whole step list (found by the text
    check on the TPU corpus). Put the next block on its own line after a blank line."""
    out: list[str] = []
    for line in lines:
        m = HTML_BLOCK_GLUE.match(line)
        if m:
            out.extend([m.group(1) + m.group(2), "", m.group(1) + m.group(3)])
        else:
            out.append(line)
    return out


def mark_generated(lines: list[str], md_rel: str, children: set[str]) -> list[str]:
    """Wrap the navigation DITA-OT generates from the map (child links, parent link, related
    links) in a `manifest-nav` container, so authored text and generated links can be checked
    separately, and give each figure its title back as a caption.

    DITA-OT's Markdown writer keeps a figure title only as each image's tooltip; its HTML5
    output shows it as a caption. The text check found the loss on the TPU corpus."""
    here = Path(md_rel).parent
    edits: list[_Edit] = []
    n = len(lines)
    rel = next((i for i, ln in enumerate(lines) if ln.strip() == "**Related information**"), None)
    end = n
    if rel is not None:
        while end > rel and not lines[end - 1].strip():
            end -= 1
        edits.append(_Edit(rel, end, _wrap(NAV_CLASS, lines[rel:end])))
    limit = rel if rel is not None else n
    i = 0
    while i < limit:
        line = lines[i]
        if line.startswith("**Parent topic:**"):
            edits.append(_Edit(i, i + 1, _wrap(NAV_CLASS, [line])))
            i += 1
            continue
        if CHILD_LINK.match(line):
            j, targets = i, []
            while j < limit and (cm := CHILD_LINK.match(lines[j])):
                targets.append(os.path.normpath(here / cm.group(1)))
                j += 1
                while j < limit and lines[j].strip() and not lines[j].startswith(("-", "**")):
                    j += 1
            if targets and all(t in children for t in targets):
                edits.append(_Edit(i, j, _wrap(NAV_CLASS, lines[i:j])))
                i = j
                continue
        im = IMAGE_LINE.match(line)
        if im and im.group(2):
            title, j, last = im.group(2), i, i
            while j < limit:
                jm = IMAGE_LINE.match(lines[j])
                if jm and jm.group(2) == title:
                    last = j
                    j += 1
                elif not lines[j].strip():
                    j += 1
                else:
                    break
            caption = ["{." + FIGURE_CLASS + "-caption}", title, ""]
            edits.append(_Edit(i, last + 1, _wrap(FIGURE_CLASS, lines[i : last + 1], caption)))
            i = last + 1
            continue
        i += 1
    for ed in sorted(edits, key=lambda x: x.start, reverse=True):
        lines[ed.start : ed.end] = ed.new
    return lines


# ---------------------------------------------------------------- keys, conrefs, IDs, headings


def substitution_name(key: str) -> str:
    """Jinja2 reads `a-b` as subtraction, so key names become identifiers (verified on Sphinx 9)."""
    name = re.sub(r"\W", "_", key)
    return name if name and not name[0].isdigit() else f"k_{name}"


def include_name(normalised: str) -> str:
    """'reuse/tpu-warehouse.dita#tpu-warehouse/note-slice-shape' -> 'tpu-warehouse--note-slice-shape'."""
    path, _, frag = normalised.partition("#")
    tid, _, eid = frag.partition("/")
    stem = Path(path).stem
    return f"{stem}--{eid}" if eid else (tid or stem)


def apply_tokens(
    lines: list[str],
    owner: str,
    topic_id: str | None,
    by_token: dict[str, dict[str, Any]],
    rel_includes: dict[str, str],
    inline_conref_names: dict[str, str],
    result: RestoreResult,
) -> list[str]:
    out: list[str] = []
    in_fence = False
    fence_open_at = -1
    fence_needs_subs = False
    in_include = owner.startswith(INCLUDES_DIR)

    def key_name(tok: str) -> str:
        return substitution_name(str(by_token[tok]["detail"]["key"]))

    for line in lines:
        fm = FENCE.match(line)
        if fm and not in_fence:
            in_fence, fence_open_at, fence_needs_subs = True, len(out), False
            out.append(line)
            continue
        if fm and in_fence:
            if fence_needs_subs:
                ind, ticks, lang = FENCE.match(out[fence_open_at]).groups()  # type: ignore[union-attr]
                out[fence_open_at] = f"{ind}{ticks}{{code-block}} {lang.strip() or 'text'}"
                out.insert(fence_open_at + 1, f"{ind}:substitutions:")
            in_fence = False
            out.append(line)
            continue

        if in_fence:
            for m in list(TOKEN.finditer(line)):
                tok = m.group(0)
                if by_token[tok]["kind"] == "key":
                    line = line.replace(tok, "{{" + key_name(tok) + "}}")
                    fence_needs_subs = True
                    result.mappings.append(
                        Mapping(tok, "key", owner, "{{" + key_name(tok) + "}}", "in a code block")
                    )
            out.append(line)
            continue

        # Heading attributes DITA-OT writes but MyST doesn't read: {#id .class}
        cm = HEADING_CLASSES.match(line)
        if cm:
            line = f"{cm.group(1)} {cm.group(2)}"
        hm = HEADING_ATTRS.match(line)
        if hm:
            hashes, title, hid, _classes = hm.groups()
            label = hid if (hashes == "#" or in_include or not topic_id) else f"{topic_id}__{hid}"
            if not in_include:
                if label != hid or hashes != "#":
                    # docutils rewrites '__' in IDs to '-', so a label alone can't keep DITA-OT's
                    # anchor; a raw HTML anchor passes through to the HTML output unchanged.
                    out.extend([f'<span id="{label}"></span>', ""])
                out.append(f"({label})=")
            line = f"{hashes} {title}"

        # Keys inside inline code: the whole span becomes a substitution-code role.
        def code_span(m: re.Match[str]) -> str:
            body = m.group(2)
            if not TOKEN.search(body):
                return m.group(0)
            for t in TOKEN.findall(body):
                tok = f"@@{t[0]}{t[1]}@@"
                body = body.replace(tok, "{{" + key_name(tok) + "}}")
                result.mappings.append(
                    Mapping(tok, "key", owner, "{{" + key_name(tok) + "}}", "in inline code")
                )
            return "{substitution-code}`" + body.strip() + "`"

        line = INLINE_CODE.sub(code_span, line)

        for m in list(TOKEN.finditer(line)):
            tok = m.group(0)
            entry = by_token[tok]
            kind = entry["kind"]
            if kind == "key":
                rep = "{{ " + key_name(tok) + " }}"
                line = line.replace(tok, rep)
                result.mappings.append(Mapping(tok, "key", owner, rep))
            elif kind == "id":
                if in_include:
                    line = line.replace(tok, "")
                    result.mappings.append(
                        Mapping(
                            tok,
                            "id",
                            owner,
                            "dropped",
                            "shared element ID: DITA-OT HTML5 does not publish it",
                        )
                    )
                else:
                    anchor = f"{topic_id}__{entry['detail']['id']}"
                    span = f'<span id="{anchor}"></span>'
                    img = re.search(r"!\[[^\]]*" + re.escape(tok), line)
                    if img:  # alt text can't hold HTML: anchor the image from just before it
                        line = line.replace(tok, "")
                        line = line[: img.start()] + span + line[img.start() :]
                    else:
                        line = line.replace(tok, span)
                    result.mappings.append(Mapping(tok, "id", owner, span))
            elif kind == "conref":
                norm = str(entry["detail"]["normalised"])
                if norm in inline_conref_names:
                    rep = "{{ " + inline_conref_names[norm] + " }}"
                    line = line.replace(tok, rep)
                    result.mappings.append(Mapping(tok, "conref", owner, rep, "inline conref"))
                    continue
                rest = line.replace(tok, "")
                lm = LIST_MARKER.match(rest)
                prefix = lm.group(0) if lm else re.match(r"\s*", rest).group(0)  # type: ignore[union-attr]
                tail = NOTE_LABEL.sub("", rest[len(prefix) :]).strip()
                if tail:
                    result.unhandled.append(
                        {
                            "owner": owner,
                            "token": tok,
                            "reason": f"conref shares its line with other text: {tail!r}",
                        }
                    )
                    continue
                rel = rel_includes[norm]
                inc_line = f"{prefix}```{{include}} {rel}"
                out.append(inc_line)
                line = " " * len(prefix) + "```"
                result.mappings.append(Mapping(tok, "conref", owner, f"```{{include}} {rel}", norm))
        out.append(line)

    if in_fence:
        raise RestoreError(f"{owner}: unclosed code fence")
    left = [m.group(0) for ln in out for m in TOKEN.finditer(ln)]
    if left:
        raise RestoreError(f"{owner}: tokens left after restore: {sorted(set(left))}")
    return out


def _include_body(md: str) -> list[str]:
    """Drop the wrapper title DITA-OT writes for an include topic; unwrap a single wrapped step."""
    lines = md.split("\n")
    while lines and (not lines[0].strip() or lines[0].startswith("# ")):
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines:
        m = LIST_MARKER.match(lines[0])
        if (
            m
            and m.group(2)[0].isdigit()
            and not any(LIST_MARKER.match(ln) for ln in lines[1:] if ln and not ln.startswith(" "))
        ):
            width = m.end()
            lines = [lines[0][width:]] + [
                ln[width:] if ln.startswith(" " * width) else ln for ln in lines[1:]
            ]
    return lines


# ---------------------------------------------------------------- the tool


CONF_PY = r'''"""Sphinx configuration written by MANIFEST. Values live in _manifest/substitutions.json."""

import json
from pathlib import Path

_m = Path(__file__).parent / "_manifest"
project = __PROJECT__
extensions = ["myst_parser", "sphinx_substitution_extensions"]
myst_enable_extensions = ["substitution", "colon_fence", "attrs_inline", "attrs_block", "deflist"]
smartquotes = False  # DITA-OT keeps straight quotes; curly ones would change the text
myst_substitutions = json.loads((_m / "substitutions.json").read_text(encoding="utf-8"))
exclude_patterns = ["_build", "_includes/**", "_manifest/**"]
html_title = project

# DITAVAL, as DITA-OT applies it: a page is left out of builds that publish no reference to it,
# and left out of the table of contents (but still built) where only a reltable reaches it.
import posixpath as _pp

_pages = json.loads((_m / "pages.json").read_text(encoding="utf-8"))
_build = next((b for b in _pages["builds"] if b != "unfiltered" and tags.has(b)), "unfiltered")  # noqa: F821
for _page, _builds in _pages["pages"].items():
    if _build not in _builds:
        exclude_patterns.append(_page)
_off_toc = {p[:-3] for p, b in _pages["toc"].items() if _build not in b}


def _source_read(app, docname, source):
    if docname in _off_toc:
        source[0] = "---\norphan: true\n---\n" + source[0]
    lines, out, in_toc, skip = source[0].split("\n"), [], False, False
    for line in lines:
        if line.startswith("% manifest:published-in "):
            skip = _build not in line.split(" ", 2)[2].split(",")
            continue
        if line.startswith("% manifest:end"):
            skip = False
            continue
        if skip:
            continue
        if line.startswith("```{toctree}"):
            in_toc = True
        elif in_toc and line.startswith("```"):
            in_toc = False
        elif in_toc and line.strip() and not line.startswith(":"):
            if _pp.normpath(_pp.join(_pp.dirname(docname), line.strip())) in _off_toc:
                continue
        out.append(line)
    source[0] = "\n".join(out)


def setup(app):
    app.connect("source-read", _source_read)
'''


def _toctree(entries: list[str], hidden: bool, maxdepth: int = 2) -> list[str]:
    opts = [f":maxdepth: {maxdepth}"] + ([":hidden:"] if hidden else [])
    return ["", "```{toctree}", *opts, "", *entries, "```", ""]


def restore_reuse(
    map_path: Path, convert_dir: Path, out_dir: Path, ditavals: list[Path] | None = None
) -> RestoreResult:
    model = load_map(map_path)
    src = model.path.parent
    convert = json.loads((convert_dir / "convert.json").read_text(encoding="utf-8"))
    if not convert.get("ok"):
        raise RestoreError("convert_map did not keep every placeholder; fix that first")
    ledger: list[dict[str, Any]] = convert["ledger"]
    by_token = {e["token"]: e for e in ledger if e["token"]}
    md_in = convert_dir / "markdown"

    if ditavals is None:
        ditavals = sorted(src.rglob("*.ditaval"))
    builds, problems = load_builds(ditavals)

    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "_includes").mkdir(parents=True)
    (out_dir / "_manifest").mkdir()
    if (md_in / "media").exists():
        shutil.copytree(md_in / "media", out_dir / "media")

    result = RestoreResult(
        str(out_dir),
        [
            asdict(b)
            | {
                "include": {k: sorted(v) for k, v in b.include.items()},
                "exclude": {k: sorted(v) for k, v in b.exclude.items()},
                "default_exclude": sorted(b.default_exclude),
            }
            for b in builds
        ],
    )
    for p in problems:
        result.unhandled.append({"owner": "ditaval", "token": "", "reason": p})
    for e in ledger:
        if e["kind"] == "unhandled":
            result.unhandled.append(
                {
                    "owner": e["owner"],
                    "token": "",
                    "reason": e["detail"]["reason"],
                    "element": e["element"],
                    "line": e["line"],
                }
            )

    # Substitutions: every defined key, named as an identifier, plus inline conrefs.
    names: dict[str, str] = {}
    for key, value in model.keys.items():
        name = substitution_name(key)
        if name in names and names[name] != key:
            raise RestoreError(f"keys {names[name]!r} and {key!r} both map to substitution {name!r}")
        names[name] = key
        result.substitutions[name] = value

    # Includes: one file per distinct conref target; inline targets become substitutions.
    conrefs = [e for e in ledger if e["kind"] == "conref"]
    inline_names: dict[str, str] = {}
    include_paths: dict[str, Path] = {}
    for e in conrefs:
        norm = str(e["detail"]["normalised"])
        if norm in include_paths or norm in inline_names:
            continue
        inc_id = str(e["detail"]["include"])
        owner = f"{INCLUDES_DIR}/{inc_id}.dita"
        raw = (md_in / INCLUDES_DIR / f"{inc_id}.md").read_text(encoding="utf-8")
        body = _include_body(raw)
        body = apply_conditions(
            body,
            [c for c in ledger if c["kind"] == "condition" and c["owner"] == owner],
            builds,
            result,
            owner,
        )
        body = apply_tokens(body, owner, None, by_token, {}, {}, result)
        name = include_name(norm)
        if e["element"] in INLINE_CONREF_ELEMENTS:
            text = " ".join(ln.strip() for ln in body if ln.strip())
            sub = substitution_name(name)
            result.substitutions[sub] = text
            inline_names[norm] = sub
            continue
        path = out_dir / "_includes" / f"{name}.md"
        path.write_text("\n".join(body) + "\n", encoding="utf-8")
        include_paths[norm] = path
        result.includes[f"_includes/{name}.md"] = norm

    # Topics.
    pages: dict[str, list[str]] = {}  # page -> builds that publish it (absent: every build)
    toc_only: dict[str, list[str]] = {}  # page -> builds whose table of contents list it
    findings: list[dict[str, Any]] = []
    children: dict[Path, list[str]] = {}
    top: list[str] = []
    for t in model.topics:
        if t.processing_role == "resource-only":
            continue
        rel = str(t.path.relative_to(src))
        md_rel = str(Path(rel).with_suffix(".md"))
        doc = str(Path(rel).with_suffix(""))
        (children.setdefault(t.parent, []) if t.parent else top).append(doc)
        in_toc = [b.name for b in builds if _all_visible(b, t.conditions)]
        published = set(in_toc) | {
            b.name for p, c in model.reltable_refs if p == t.path for b in builds if _all_visible(b, c)
        }
        if len(in_toc) != len(builds):
            toc_only[md_rel] = in_toc
            leaked = [b for b in published if b not in in_toc]
            if leaked:
                findings.append(
                    {
                        "kind": "reltable-keeps-filtered-topic",
                        "topic": rel,
                        "builds": sorted(leaked),
                        "detail": "The map filters this topic out of these builds, but a relationship-table "
                        "reference without the same condition keeps it published (DITA-OT does the "
                        "same: the page is built but left out of the table of contents). "
                        "Add the condition to the reltable row if the topic should be excluded.",
                    }
                )
        if len(published) != len(builds):
            pages[md_rel] = sorted(published)
    # Which builds have a generated link from page A to page B, as DITA-OT decides it: a reltable
    # link wherever B is published; a map-tree link (parent, child, family sibling) only where
    # both pages are in the table of contents. Found by the navigation check on the TPU corpus.
    all_names = [b.name for b in builds]
    entry_by_md = {
        str(Path(str(e.path.relative_to(src))).with_suffix(".md")): e
        for e in model.topics
        if e.processing_role != "resource-only"
    }
    family = {e.path for e in model.topics if e.collection == "family"}

    def link_rule(a: Any) -> Callable[[str], list[str]]:
        def builds_for(target_md: str) -> list[str]:
            b = entry_by_md.get(target_md)
            if b is None:
                return all_names
            a_md = str(Path(str(a.path.relative_to(src))).with_suffix(".md"))
            published_b = set(pages.get(target_md, all_names))
            toc_a, toc_b = set(toc_only.get(a_md, all_names)), set(toc_only.get(target_md, all_names))
            reltable = frozenset((a.path, b.path)) in model.reltable_pairs
            tree = (
                b.parent == a.path
                or a.parent == b.path
                or (a.parent is not None and a.parent == b.parent and a.parent in family)
            )
            if not reltable and not tree:
                return [n for n in all_names if n in published_b]
            ok = (published_b if reltable else set()) | ((toc_a & toc_b) if tree else set())
            return [n for n in all_names if n in ok]

        return builds_for

    # Second pass, now that every page's builds are known: write the topics.
    by_build = {b.name: b for b in builds}
    for t in model.topics:
        if t.processing_role == "resource-only":
            continue
        rel = str(t.path.relative_to(src))
        md_rel = str(Path(rel).with_suffix(".md"))
        topic_id = parse(t.path).getroot().get("id")
        lines = (md_in / md_rel).read_text(encoding="utf-8").split("\n")
        child_pages = {
            str(Path(str(k.path.relative_to(src))).with_suffix(".md"))
            for k in model.topics
            if k.parent == t.path
        }
        lines = unglue_html_blocks(lines)
        lines = mark_generated(lines, md_rel, child_pages)
        lines = apply_conditions(
            lines, [c for c in ledger if c["kind"] == "condition" and c["owner"] == rel], builds, result, rel
        )
        lines = guard_links(lines, md_rel, link_rule(t), builds, by_build, result, rel)
        target_dir = (out_dir / md_rel).parent
        rel_includes = {n: os.path.relpath(p, target_dir) for n, p in include_paths.items()}
        lines = apply_tokens(lines, rel, topic_id, by_token, rel_includes, inline_names, result)
        (out_dir / md_rel).parent.mkdir(parents=True, exist_ok=True)
        (out_dir / md_rel).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    # Navigation: the map's hierarchy as toctrees (hidden on topic pages, so their text is unchanged).
    for parent, kids in children.items():
        page = out_dir / Path(str(parent.relative_to(src))).with_suffix(".md")
        rel_kids = [os.path.relpath(out_dir / k, page.parent) for k in kids]
        page.write_text(
            page.read_text(encoding="utf-8") + "\n".join(_toctree(rel_kids, hidden=True)), encoding="utf-8"
        )
    title = (parse(model.path).getroot().findtext("title") or model.path.stem).strip()
    (out_dir / "index.md").write_text(
        "\n".join([f"# {title}", *_toctree(top, hidden=False)]) + "\n", encoding="utf-8"
    )

    (out_dir / "_manifest" / "substitutions.json").write_text(
        json.dumps(dict(sorted(result.substitutions.items())), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (out_dir / "_manifest" / "pages.json").write_text(
        json.dumps({"builds": [b.name for b in builds], "pages": pages, "toc": toc_only}, indent=2) + "\n",
        encoding="utf-8",
    )
    findings.extend(_possible_typed_copies(model, src))
    result.findings = findings
    (out_dir / "conf.py").write_text(CONF_PY.replace("__PROJECT__", repr(title)), encoding="utf-8")
    result.write(out_dir / "_manifest" / "restore.json")
    return result
