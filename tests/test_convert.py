"""Tool 2, convert_map: placeholders survive DITA-OT (design doc §5.2: 'test first in the MVP')."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from lxml import etree

from manifest.placeholders import TOKEN, Ledger, tokenize
from manifest.tools.convert import ConvertError, convert_map, find_dita
from manifest.tools.inventory import inventory_map

ROOT = Path(__file__).resolve().parent.parent
TPU_MAP = ROOT / "corpus" / "tpu" / "tpu.ditamap"

try:
    DITA: str | None = find_dita()
except ConvertError:
    DITA = None
needs_dita = pytest.mark.skipif(DITA is None or not TPU_MAP.exists(), reason="needs DITA-OT and the corpus")


def tok(xml: str) -> tuple[str, Ledger]:
    root = etree.fromstring(xml)
    ledger = Ledger()
    tokenize(root, ledger, "t.dita")
    return etree.tostring(root, encoding="unicode"), ledger


# ---------------------------------------------------------------- tokenize: one rule per lesson


def test_key_becomes_a_numbered_token_and_survives_markdown_escaping() -> None:
    out, led = tok(
        '<topic id="t"><title>T</title><body><p>Speed <keyword keyref="v5e-speed"/>.</p></body></topic>'
    )
    assert "<p>Speed @@K1@@.</p>" in out
    assert TOKEN.fullmatch("@@K1@@") and led.entries[0].detail["key"] == "v5e-speed"


def test_condition_markers_never_go_inside_an_inline_key() -> None:
    """Regression (TPU corpus): an end marker inside the last <keyword> gave it content, DITA-OT
    resolved the key and the marker vanished."""
    xml = (
        '<task id="t"><title>T</title><taskbody><steps><step platform="v5e"><cmd>Run:</cmd><info>'
        '<codeblock>--version=<keyword keyref="v5e-runtime"/></codeblock></info></step><step><cmd>Next.</cmd></step></steps>'
        "</taskbody></task>"
    )
    out, led = tok(xml)
    assert "--version=@@K2@@@@E1@@</codeblock>" in out
    assert [e.kind for e in led.entries] == ["condition", "key"]


def test_condition_on_a_conref_wraps_the_conref_token() -> None:
    """Regression: replacing the conref content after placing markers wiped the markers out."""
    out, led = tok(
        '<topic id="t"><title>T</title><body><note platform="tpu7x" conref="w.dita#w/n"/></body></topic>'
    )
    assert '<note platform="tpu7x">@@B2@@@@C1@@@@E2@@</note>' in out


def test_marker_skips_a_child_that_will_be_replaced_by_a_conref() -> None:
    """Regression: the step's last text element was a conref note; its end marker was wiped."""
    xml = (
        '<task id="t"><title>T</title><taskbody><steps><step platform="tpu7x"><cmd>Create.</cmd><info>'
        '<note conref="w.dita#w/n"/></info></step><step><cmd>Next.</cmd></step></steps></taskbody></task>'
    )
    out, _ = tok(xml)
    assert "<note>@@C2@@</note>@@E1@@</info>" in out


def test_figure_id_marker_avoids_the_title_dita_ot_repeats() -> None:
    out, _ = tok(
        '<topic id="t"><title>T</title><body><fig id="f"><title>Fig</title>'
        '<image href="a.png"><alt>Alt</alt></image></fig></body></topic>'
    )
    assert "<title>Fig</title>" in out and "<alt>@@I1@@Alt</alt>" in out


def test_section_and_topic_ids_are_left_to_dita_ot() -> None:
    _, led = tok(
        '<topic id="t"><title>T</title><body><section id="s"><title>S</title><p id="p">x</p></section></body></topic>'
    )
    assert [e.detail.get("id") for e in led.entries] == ["p"]


@pytest.mark.parametrize(
    ("xml", "reason"),
    [
        (
            '<topic id="t"><title>T</title><body><object data="v.mp4"/></body></topic>',
            "no Markdown equivalent",
        ),
        (
            '<topic id="t"><title>T</title><body><p conref="w.dita#w/a" conrefend="w.dita#w/b"/></body></topic>',
            "conref range",
        ),
        (
            '<topic id="t"><title>T</title><body><p><keyword keyref="k">fallback</keyword></p></body></topic>',
            "keyref on <keyword> with content",
        ),
    ],
)
def test_what_cannot_be_carried_is_recorded_never_dropped(xml: str, reason: str) -> None:
    _, led = tok(xml)
    assert any(e.kind == "unhandled" and reason in str(e.detail["reason"]) for e in led.entries)


# ---------------------------------------------------------------- the real corpus through DITA-OT 4.4


def _tree_hash(folder: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(folder)).encode() + p.read_bytes())
    return h.hexdigest()


@pytest.fixture(scope="module")
def converted(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    before = _tree_hash(TPU_MAP.parent)
    res = convert_map(TPU_MAP, tmp_path_factory.mktemp("convert"), DITA)
    return res, before


@needs_dita
def test_every_placeholder_survives_exactly_once_in_every_file(converted) -> None:  # type: ignore[no-untyped-def]
    res, _ = converted
    assert res.ok, [f for f in res.files if not f.ok]
    t = res.totals()
    assert t["files"] == t["files_ok"] == 30  # 24 published topics + 6 includes
    assert t["missing"] == t["repeated"] == 0


@needs_dita
def test_every_key_and_conref_in_published_topics_is_carried(converted) -> None:  # type: ignore[no-untyped-def]
    res, _ = converted
    inv = inventory_map(TPU_MAP)
    published = [tp for tp in inv.topics if tp.processing_role == "normal"]
    in_topics = [e for e in res.ledger if not e["owner"].startswith("_manifest_includes")]
    assert sum(e["kind"] == "key" for e in in_topics) == sum(len(tp.keyrefs) for tp in published)
    assert sum(e["kind"] == "conref" for e in in_topics) == sum(len(tp.conrefs) for tp in published) == 12
    assert (
        sum(e["kind"] == "condition" for e in in_topics) == sum(len(tp.conditions) for tp in published) == 11
    )


@needs_dita
def test_shared_content_is_converted_once_per_target(converted) -> None:  # type: ignore[no-untyped-def]
    res, _ = converted
    conrefs = [e for e in res.ledger if e["kind"] == "conref"]
    assert len({e["detail"]["normalised"] for e in conrefs}) == len(res.includes) == 6
    assert all(e["detail"]["include"] in res.includes for e in conrefs)


@needs_dita
def test_what_has_no_rule_is_unhandled_and_named(converted) -> None:  # type: ignore[no-untyped-def]
    """The video, which plain DITA-OT drops, and the choice table's ID, which has no text to carry
    an anchor until a mapping rule turns the choice table into a simple table."""
    res, _ = converted
    assert sorted((e["owner"], e["element"]) for e in res.unhandled) == [
        ("topics/osfp-transceiver.dita", "object"),
        ("topics/tpu-v5e-slice-planning.dita", "choicetable"),
    ]


@needs_dita
def test_the_dita_source_is_never_changed(converted) -> None:  # type: ignore[no-untyped-def]
    _, before = converted
    assert _tree_hash(TPU_MAP.parent) == before


@needs_dita
def test_dita_ot_version_is_recorded(converted) -> None:  # type: ignore[no-untyped-def]
    res, _ = converted
    assert res.dita_ot.startswith("4.4")


# ---------------------------------------------------------------- mapping rules found by the text check (2d)


def test_optional_step_keeps_its_label() -> None:
    """DITA-OT HTML5 shows 'Optional:'; its Markdown writer drops it, hiding that the step is optional."""
    out, _ = tok(
        '<task id="t"><title>T</title><taskbody><steps><step importance="optional"><cmd>Add a flag.</cmd>'
        "</step><step><cmd>Next.</cmd></step></steps></taskbody></task>"
    )
    assert "<cmd><b>Optional:</b> Add a flag.</cmd>" in out


def test_inline_code_markup_is_flattened() -> None:
    """DITA-OT's Markdown writer turned <codeph>--zone=<varname>ZONE</varname></codeph> into `--zone=*ZONE*`."""
    out, _ = tok(
        '<topic id="t"><title>T</title><body><p><codeph>list --zone=<varname>ZONE</varname></codeph></p>'
        "</body></topic>"
    )
    assert "<codeph>list --zone=ZONE</codeph>" in out
