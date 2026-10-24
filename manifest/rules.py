"""Mapping rules: how MANIFEST learns to migrate an element it had no rule for.

A rule is an XSLT 1.0 template that rewrites one kind of DITA element into DITA that DITA-OT's
Markdown writer handles, for example <choicetable> into <simpletable>. XSLT is what DITA writers
already use for DITA-OT plug-ins, so a rule is something a writer can read and review.

Rules are applied to convert_map's working copy, never to the source. The checks still compare
against DITA-OT HTML5 of the untouched source, so a rule can only make a topic pass by making
the migrated page say what the published page says.

Gemini proposes rules (build step 3); a writer approves them. Because a model writes them, a
rule is held to strict limits before it ever runs:
    - only top-level <xsl:template match="..."> elements; no import, include or output control
    - no document(), no extension functions or namespaces other than XSLT's own
    - executed with lxml's XSLT access control set to deny every file and network access
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree

XSL = "http://www.w3.org/1999/XSL/Transform"
STATUSES = ("proposed", "approved", "rejected")
FORBIDDEN = re.compile(r"\bdocument\s*\(|\bunparsed-text\s*\(|\bsystem-property\s*\(", re.I)
IDENTITY = f"""<xsl:stylesheet version="1.0" xmlns:xsl="{XSL}">
  <xsl:template match="@*|node()">
    <xsl:copy><xsl:apply-templates select="@*|node()"/></xsl:copy>
  </xsl:template>
</xsl:stylesheet>"""


class RuleError(ValueError):
    pass


@dataclass
class Rule:
    id: str
    match: str  # the element the rule rewrites, for people and for the report
    description: str
    xslt: str  # one or more <xsl:template> elements
    status: str = "proposed"
    provenance: dict[str, str] = field(default_factory=dict)  # run, topic, proposed_by, approved_by, at

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2) + "\n"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Rule:
        rule = cls(**{k: d[k] for k in ("id", "match", "description", "xslt") if k in d},
                   status=d.get("status", "proposed"), provenance=dict(d.get("provenance", {})))  # fmt: skip
        validate(rule)
        return rule


def _templates(rule: Rule) -> list[etree._Element]:
    try:
        wrapper = etree.fromstring(
            f'<xsl:stylesheet version="1.0" xmlns:xsl="{XSL}">{rule.xslt}</xsl:stylesheet>'
        )
    except etree.XMLSyntaxError as exc:
        raise RuleError(f"rule {rule.id}: the XSLT is not well-formed: {exc}") from exc
    return list(wrapper)


def validate(rule: Rule) -> None:
    """Raise RuleError unless the rule is safe and well-formed. Called before any rule runs."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", rule.id):
        raise RuleError(f"rule id {rule.id!r}: use 3-64 lowercase letters, digits and hyphens")
    if rule.status not in STATUSES:
        raise RuleError(f"rule {rule.id}: status must be one of {STATUSES}")
    if FORBIDDEN.search(rule.xslt):
        raise RuleError(f"rule {rule.id}: document(), unparsed-text() and system-property() are not allowed")
    templates = _templates(rule)
    if not templates:
        raise RuleError(f"rule {rule.id}: no <xsl:template> found")
    for t in templates:
        if not isinstance(t.tag, str) or t.tag != f"{{{XSL}}}template":
            raise RuleError(f"rule {rule.id}: only <xsl:template> is allowed at the top level, not {t.tag}")
        if not t.get("match"):
            raise RuleError(f"rule {rule.id}: every template needs a match pattern")
        for el in t.iter():
            if not isinstance(el.tag, str):
                continue
            ns = etree.QName(el).namespace
            if ns and ns != XSL:
                raise RuleError(f"rule {rule.id}: namespace {ns} is not allowed (no extensions)")
            if ns == XSL and etree.QName(el).localname in ("import", "include", "result-document", "output"):
                raise RuleError(f"rule {rule.id}: <xsl:{etree.QName(el).localname}> is not allowed")
        for attr in (a for el in t.iter() if isinstance(el.tag, str) for a in el.attrib):
            if etree.QName(attr).namespace not in (None, XSL):
                raise RuleError(f"rule {rule.id}: namespaced attribute {attr!s} is not allowed")


class RuleSet:
    """The rules that run, compiled once into one stylesheet on top of the identity transform."""

    def __init__(self, rules: list[Rule]) -> None:
        for r in rules:
            validate(r)
        self.rules = rules
        sheet = etree.fromstring(IDENTITY)
        for r in rules:
            for t in _templates(r):
                sheet.append(t)
        try:
            # lxml-stubs 0.5 don't declare these keywords; lxml accepts them (lxml.de/xpathxslt.html).
            deny = etree.XSLTAccessControl(  # type: ignore[call-arg]
                read_file=False, write_file=False, create_dir=False, read_network=False, write_network=False
            )
            self._xslt = etree.XSLT(sheet, access_control=deny)
        except etree.XSLTParseError as exc:
            raise RuleError(f"the rules don't compile as XSLT: {exc}") from exc

    def matches(self, root: etree._Element) -> dict[str, int]:
        """How many elements each rule's match pattern selects in this topic."""
        counts: dict[str, int] = {}
        for r in self.rules:
            n = sum(1 for el in root.iter() if isinstance(el.tag, str) and str(el.tag) == r.match)
            if n:
                counts[r.id] = n
        return counts

    def apply(self, tree: etree._ElementTree) -> etree._ElementTree:
        if not self.rules:
            return tree
        try:
            result = self._xslt(tree)
        except etree.XSLTApplyError as exc:
            raise RuleError(f"a rule failed while running: {exc}") from exc
        root = result.getroot()
        if root is None:
            raise RuleError("the rules produced an empty document")
        out = etree.ElementTree(root)
        out.docinfo.URL = tree.docinfo.URL
        return out


def load_rules(folder: Path, statuses: tuple[str, ...] = ("approved",)) -> list[Rule]:
    """Rules stored as one JSON file each. By default only approved rules run."""
    if not folder.exists():
        return []
    rules = [Rule.from_dict(json.loads(p.read_text(encoding="utf-8"))) for p in sorted(folder.glob("*.json"))]
    return [r for r in rules if r.status in statuses]


def save_rule(rule: Rule, folder: Path) -> Path:
    validate(rule)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{rule.id}.json"
    path.write_text(rule.to_json(), encoding="utf-8")
    return path
