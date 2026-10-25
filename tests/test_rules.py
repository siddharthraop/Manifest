"""Mapping rules: validated XSLT templates, applied to the working copy only.

The safety tests matter most: Gemini writes these rules, so a rule that could read a file, reach
the network or run an extension must be refused before it runs.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from lxml import etree

from manifest.rules import Rule, RuleError, RuleSet, load_rules, save_rule, validate
from manifest.tools.convert import ConvertError, convert_map, find_dita

ROOT = Path(__file__).resolve().parent.parent
TPU_MAP = ROOT / "corpus" / "tpu" / "tpu.ditamap"
FIXTURES = Path(__file__).parent / "fixtures" / "rules"
try:
    DITA: str | None = find_dita()
except ConvertError:
    DITA = None
needs_dita = pytest.mark.skipif(DITA is None or not TPU_MAP.exists(), reason="needs DITA-OT and the corpus")

RENAME = (
    '<xsl:template match="hazardstatement"><note type="caution"><xsl:apply-templates/></note></xsl:template>'
)


def rule(xslt: str = RENAME, **kw: str) -> Rule:
    return Rule(id=kw.get("id", "hazard-to-note"), match="hazardstatement", description="d", xslt=xslt,
                status=kw.get("status", "approved"))  # fmt: skip


# ---------------------------------------------------------------- safety


@pytest.mark.parametrize(
    ("xslt", "message"),
    [
        ('<xsl:template match="p"><xsl:copy-of select="document(\'/etc/passwd\')"/></xsl:template>', "document()"),
        ('<xsl:template match="p"><xsl:value-of select="document (\'http://x\')"/></xsl:template>', "document()"),
        ('<xsl:import href="evil.xsl"/>', "only <xsl:template>"),
        ('<xsl:template match="p"><xsl:include href="x.xsl"/></xsl:template>', "<xsl:include>"),
        ('<xsl:template match="p" xmlns:ex="http://exslt.org/common"><ex:document href="x"/></xsl:template>',
         "namespace http://exslt.org/common"),
        ('<xsl:template match="p"><xsl:value-of select="system-property(\'xsl:vendor\')"/></xsl:template>',
         "system-property()"),
        ("<xsl:template><p/></xsl:template>", "match pattern"),
        ('<xsl:template match="p"><p>', "not well-formed"),
        ("", "no <xsl:template>"),
    ],
)  # fmt: skip
def test_unsafe_or_broken_rules_are_refused(xslt: str, message: str) -> None:
    with pytest.raises(RuleError, match=message.replace("(", r"\(").replace(")", r"\)")):
        validate(rule(xslt))


def test_rule_ids_and_statuses_are_checked() -> None:
    with pytest.raises(RuleError, match="rule id"):
        validate(rule(id="Bad ID!"))
    with pytest.raises(RuleError, match="status"):
        validate(rule(status="maybe"))


def test_rules_run_with_file_access_denied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Defence in depth: even if a document() call slipped past validation, the RuleSet's own XSLT
    access control refuses to read the file, and the run fails loudly instead of leaking it."""
    secret = tmp_path / "secret.xml"
    secret.write_text("<s>secret</s>")
    monkeypatch.setattr("manifest.rules.validate", lambda r: None)  # pretend validation missed it
    sneaky = rule(
        f'<xsl:template match="p"><xsl:copy-of select="document(\'{secret.as_uri()}\')"/></xsl:template>'
    )
    rs = RuleSet([sneaky])
    with pytest.raises(RuleError, match="denied"):
        rs.apply(etree.fromstring("<topic><p/></topic>").getroottree())


# ---------------------------------------------------------------- behaviour


def test_a_rule_rewrites_only_what_it_matches() -> None:
    tree = etree.fromstring(
        '<task id="t"><title>T</title><taskbody><context><p>Keep.</p>'
        '<hazardstatement type="caution"><messagepanel><typeofhazard>Hot</typeofhazard></messagepanel>'
        "</hazardstatement></context></taskbody></task>"
    ).getroottree()
    rs = RuleSet([rule()])
    assert rs.matches(tree.getroot()) == {"hazard-to-note": 1}
    out = etree.tostring(rs.apply(tree), encoding="unicode")
    assert "<p>Keep.</p>" in out and '<note type="caution">' in out and "hazardstatement" not in out


def test_rules_load_approved_only_and_round_trip(tmp_path: Path) -> None:
    save_rule(rule(id="approved-one"), tmp_path)
    save_rule(rule(id="proposed-one", status="proposed"), tmp_path)
    assert [r.id for r in load_rules(tmp_path)] == ["approved-one"]
    assert [r.id for r in load_rules(tmp_path, ("approved", "proposed"))] == ["approved-one", "proposed-one"]
    assert json.loads((tmp_path / "approved-one.json").read_text())["status"] == "approved"


def test_the_fixture_rules_are_valid() -> None:
    rules = load_rules(FIXTURES)
    assert sorted(r.match for r in rules) == ["choicetable", "hazardstatement"]
    RuleSet(rules)  # compiles


# ---------------------------------------------------------------- in convert_map, on the corpus


def _tree_hash(folder: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(folder)).encode() + p.read_bytes())
    return h.hexdigest()


@needs_dita
def test_rules_apply_in_the_working_copy_and_are_recorded(tmp_path: Path) -> None:
    before = _tree_hash(TPU_MAP.parent)
    res = convert_map(TPU_MAP, tmp_path, DITA, RuleSet(load_rules(FIXTURES)))
    assert res.ok
    assert sorted((a["rule"], a["topic"]) for a in res.rules_applied) == [
        ("choicetable-to-simpletable", "topics/tpu-v5e-slice-planning.dita"),
        ("hazardstatement-to-simpletable", "topics/tpu-v5e-slice-planning.dita"),
    ]
    # With the choice table now a simple table, its ID has a cell to anchor to: no longer unhandled.
    assert [(e["owner"], e["element"]) for e in res.unhandled] == [("topics/osfp-transceiver.dita", "object")]
    work = (tmp_path / "work" / "topics" / "tpu-v5e-slice-planning.dita").read_text()
    assert "<!DOCTYPE task" in work, "DITA-OT needs the DOCTYPE to give elements their class"
    assert "<simpletable" in work and "choicetable" not in work
    assert _tree_hash(TPU_MAP.parent) == before, "the source is never changed"
