"""Placeholders: carry reuse, conditions and IDs through DITA-OT without letting it resolve them.

Each placeholder is a short numbered token, such as @@K12@@, whose meaning lives in a ledger.
Tokens use only '@', capital letters and digits, which DITA-OT's Markdown writer leaves alone;
it escapes characters such as '#', '_' and '*' (ADR 0002). The token kinds:

    K  key reference         the keyword is replaced by the token
    C  content reference     the element's content is replaced by the token
    B/E condition begin/end  wrap the conditioned element's text
    I  element ID            an ID that DITA-OT's Markdown output drops

Anything MANIFEST cannot carry is recorded as an 'unhandled' entry. Nothing is dropped silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

from manifest.dita import PROFILE_ATTRS

TOKEN = re.compile(r"@@([KCBEI])(\d+)@@")

# Elements a key reference can be replaced in by plain text (DITA: keyref on a phrase-level
# element with no content means "show the key's text").
INLINE_KEY_ELEMENTS = {"keyword", "ph", "term", "text", "abbreviated-form"}

# Block-level elements whose text DITA-OT renders as-is: the only places a condition or ID
# marker may go. Never inline elements: a marker inside an empty <keyword keyref> gives it
# content, DITA-OT then resolves the key, and the marker is lost (found on the TPU corpus).
BLOCK_TEXT = {
    "p",
    "cmd",
    "entry",
    "stentry",
    "title",
    "li",
    "dt",
    "dd",
    "note",
    "codeblock",
    "pre",
    "screen",
    "shortdesc",
    "desc",
    "alt",
    "info",
    "stepresult",
    "stepxmp",
    "lq",
    "fn",
    "msgblock",
    "sli",
    "lines",
    "context",
    "prereq",
    "postreq",
    "result",
}
INLINE_TEXT = {"ph", "keyword", "term", "codeph", "cite", "q", "b", "i", "u", "varname"}

# Titles DITA-OT repeats in its output (a figure title is copied onto every image in the
# figure), so an ID marker there would appear more than once.
REPEATED_TITLES = {"fig"}

# DITA-OT's Markdown output already keeps these IDs as {#id} on headings.
IDS_KEPT_BY_DITA_OT = {"section", "example"}

# A conref element whose content is replaced needs a child that can hold text, or DITA-OT
# renders nothing. Map the element to the child to create.
CONTAINER_CHILD = {"step": "cmd", "substep": "cmd", "ul": "li", "ol": "li", "sl": "sli", "steps": "step"}

UNSUPPORTED_ELEMENTS = {"object"}  # no Markdown equivalent; see the US-03 review rule


@dataclass
class Entry:
    token: str
    kind: str  # key, conref, condition, id, unhandled
    owner: str  # output file that must contain the token, e.g. "topics/a.dita" or "_includes/inc-1"
    element: str
    line: int | None
    detail: dict[str, str | list[str]] = field(default_factory=dict)


@dataclass
class Ledger:
    entries: list[Entry] = field(default_factory=list)
    _n: int = 0

    def new(self, kind_letter: str) -> str:
        self._n += 1
        return f"@@{kind_letter}{self._n}@@"

    def add(self, token: str, kind: str, owner: str, el: etree._Element, **detail: str | list[str]) -> None:
        line = el.sourceline if isinstance(el.sourceline, int) else None
        self.entries.append(Entry(token, kind, owner, str(el.tag), line, dict(detail)))

    def unhandled(self, owner: str, el: etree._Element, reason: str) -> None:
        line = el.sourceline if isinstance(el.sourceline, int) else None
        self.entries.append(Entry("", "unhandled", owner, str(el.tag), line, {"reason": reason}))

    def expected(self) -> dict[str, list[str]]:
        """Owner -> tokens that must each appear exactly once in its output."""
        out: dict[str, list[str]] = {}
        for e in self.entries:
            if e.kind == "condition":
                out.setdefault(e.owner, []).extend([e.token, e.token.replace("@@B", "@@E")])
            elif e.token:
                out.setdefault(e.owner, []).append(e.token)
        return out


def _prepend_text(el: etree._Element, s: str) -> None:
    el.text = s + (el.text or "")


def _append_text(el: etree._Element, s: str) -> None:
    children = [c for c in el if isinstance(c.tag, str)]
    if children:
        last = children[-1]
        last.tail = (last.tail or "") + s
    else:
        el.text = (el.text or "") + s


def _replace_with_text(el: etree._Element, s: str) -> None:
    """Remove el, keeping its tail, and put s where it was."""
    parent = el.getparent()
    assert parent is not None
    prev = el.getprevious()
    text = s + (el.tail or "")
    if prev is not None:
        prev.tail = (prev.tail or "") + text
    else:
        parent.text = (parent.text or "") + text
    parent.remove(el)


def _will_be_replaced(d: etree._Element, stop: etree._Element) -> bool:
    """True if d is, or sits inside, a conref element below `stop`: its content is about to be
    replaced by a token, so a marker placed there would be wiped out (found on the TPU corpus)."""
    node: etree._Element | None = d
    while node is not None and node is not stop:
        if node.get("conref"):
            return True
        node = node.getparent()
    return False


def _text_slots(el: etree._Element) -> list[etree._Element]:
    """Block-level descendants (or self) that can carry a marker, in document order."""
    return [
        d
        for d in el.iter()
        if isinstance(d.tag, str) and str(d.tag) in BLOCK_TEXT and not _will_be_replaced(d, el)
    ]


def _in_link_text(el: etree._Element, root: etree._Element) -> bool:
    """True for content of the topic's own title or short description. DITA-OT copies both into
    other topics' link text (parent links, related links, child link lists)."""
    parts = [root.find("title"), root.find("shortdesc")]
    return any(p is not None and any(a is p for a in el.iterancestors()) for p in parts)


def tokenize(root: etree._Element, ledger: Ledger, owner: str) -> None:
    """Replace reuse, conditions and droppable IDs in a parsed topic with tokens, in place."""
    in_metadata: set[etree._Element] = set()
    for el in list(root.iter()):
        if not isinstance(el.tag, str) or el in in_metadata:
            continue
        tag = str(el.tag)
        if tag in ("prolog", "topicmeta"):
            in_metadata.update(el.iter())
            continue
        if el.getparent() is None and el is not root:
            continue  # already removed with an ancestor

        if tag in UNSUPPORTED_ELEMENTS:
            ledger.unhandled(owner, el, f"<{tag}> has no Markdown equivalent; DITA-OT drops it")

        conref = el.get("conref")
        if conref:
            if el.get("conrefend") or el.get("conaction"):
                ledger.unhandled(owner, el, "conref range or conref push: no mapping rule")
                continue
            # Replace the content first; a condition on the same element then wraps the token.
            token = ledger.new("C")
            ledger.add(token, "conref", owner, el, target=conref)
            del el.attrib["conref"]
            for child in list(el):
                el.remove(child)
            el.text = None
            child_tag = CONTAINER_CHILD.get(tag)
            if child_tag:
                etree.SubElement(el, child_tag).text = token
            else:
                el.text = token

        # Conditions wrap whatever the element now renders as (including a conref token).
        for attr in PROFILE_ATTRS:
            values = el.get(attr)
            if not values:
                continue
            slots = _text_slots(el)
            if not slots and tag not in INLINE_TEXT:
                ledger.unhandled(owner, el, f"@{attr}={values}: no text to carry a condition marker")
                continue
            first = slots[0] if slots else el
            last = slots[-1] if slots else el
            begin = ledger.new("B")
            ledger.add(begin, "condition", owner, el, attribute=attr, values=values.split())
            _prepend_text(first, begin)
            _append_text(last, begin.replace("@@B", "@@E"))

        if conref:
            continue  # the element's own content is gone; nothing else in it to tokenize

        keyref = el.get("keyref")
        if keyref:
            has_content = bool((el.text or "").strip()) or any(isinstance(c.tag, str) for c in el)
            if tag in INLINE_KEY_ELEMENTS and not has_content:
                token = ledger.new("K")
                ledger.add(
                    token,
                    "key",
                    owner,
                    el,
                    key=keyref.split("/")[0],
                    in_link_text="yes" if _in_link_text(el, root) else "no",
                )
                _replace_with_text(el, token)
                continue
            ledger.unhandled(owner, el, f"keyref on <{tag}> with content or as a link: no mapping rule yet")

        if el.get("id") and el is not root and tag not in IDS_KEPT_BY_DITA_OT:
            slots = _text_slots(el)
            if tag in REPEATED_TITLES:
                slots = [d for d in slots if not (str(d.tag) == "title" and d.getparent() is el)]
            if slots or tag in INLINE_TEXT:
                token = ledger.new("I")
                ledger.add(token, "id", owner, el, id=el.get("id") or "")
                _prepend_text(slots[0] if slots else el, token)
            else:
                ledger.unhandled(owner, el, f"id={el.get('id')}: no text to carry an anchor marker")
