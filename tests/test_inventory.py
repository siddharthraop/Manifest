"""Tool 1, inventory_map. The 'before' picture every check relies on (US-02, US-06, US-07)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from manifest.cli import main
from manifest.tools.inventory import inventory_map

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
TPU_MAP = CORPUS / "tpu" / "tpu.ditamap"

needs_corpus = pytest.mark.skipif(not TPU_MAP.exists(), reason="corpus submodule not checked out")


# ---------------------------------------------------------------- the real corpus


@pytest.fixture(scope="module")
def tpu():  # type: ignore[no-untyped-def]
    return inventory_map(TPU_MAP)


@pytest.fixture(scope="module")
def corpus_inventory(tmp_path_factory: pytest.TempPathFactory) -> dict:  # type: ignore[type-arg]
    """Run the corpus's own, independent inventory tool and read its result."""
    subprocess.run(
        [sys.executable, str(CORPUS / "tools" / "inventory.py")], cwd=CORPUS, check=True, capture_output=True
    )
    data: dict = json.loads((CORPUS / "build" / "inventory.json").read_text())  # type: ignore[type-arg]
    return data["tpu"]


@needs_corpus
def test_every_mapped_topic_is_inventoried(tpu) -> None:  # type: ignore[no-untyped-def]
    # 33 DITA files in tpu/ minus the root map, key map, 2 subject maps and 4 DITAVAL files.
    assert tpu.totals["topics"] == 25
    assert [t.path for t in tpu.topics][:2] == [
        "topics/tpu-overview.dita",
        "topics/tpu-generations-compared.dita",
    ]
    resource_only = {t.path for t in tpu.topics if t.processing_role == "resource-only"}
    assert resource_only == {"reuse/tpu-warehouse.dita", "reuse/tpu-task-warehouse.dita"}


@needs_corpus
def test_key_references_match_the_corpus_tool(tpu, corpus_inventory) -> None:  # type: ignore[no-untyped-def]
    assert tpu.totals["keyrefs"] == corpus_inventory["keyref_uses"] == 169
    assert tpu.totals["keys_defined"] == len(corpus_inventory["keys"]) == 75
    assert all(k.defined for t in tpu.topics for k in t.keyrefs), "every key reference has a definition"


@needs_corpus
def test_conrefs_match_the_corpus_tool_and_all_resolve(tpu, corpus_inventory) -> None:  # type: ignore[no-untyped-def]
    ours = sorted((t.path, c.target) for t in tpu.topics for c in t.conrefs)
    theirs = sorted((c["file"].removeprefix("tpu/"), c["target"]) for c in corpus_inventory["conrefs"])
    assert ours == theirs and len(ours) == 11
    assert all(c.resolves for t in tpu.topics for c in t.conrefs)


@needs_corpus
def test_condition_values_reconcile_with_the_corpus_tool(tpu, corpus_inventory) -> None:  # type: ignore[no-untyped-def]
    """The corpus counts attribute values in every file; MANIFEST counts conditioned elements in topics
    and cascades the map's conditions onto topics. Counted the same way, they must agree."""
    in_topics = sum(len(c.values) for t in tpu.topics for c in t.conditions)
    corpus_total = sum(n for vals in corpus_inventory["profiling"].values() for n in vals.values())
    map_topicref_values = 4  # four family topicrefs carry one @platform value each in tpu.ditamap
    assert in_topics + map_topicref_values == corpus_total == 16
    assert len([c for t in tpu.topics for c in t.conditions]) == 11  # conditioned elements


@needs_corpus
def test_map_conditions_cascade_to_child_topics(tpu) -> None:  # type: ignore[no-untyped-def]
    by_path = {t.path: t for t in tpu.topics}
    assert by_path["topics/tpu7x-memory.dita"].map_conditions == {"platform": ["tpu7x"]}
    assert by_path["topics/tpu-create-vm.dita"].map_conditions == {}


@needs_corpus
def test_ids_include_topic_and_element_ids(tpu) -> None:  # type: ignore[no-untyped-def]
    spec = next(t for t in tpu.topics if t.path == "topics/tpu7x-specifications.dita")
    assert spec.ids == ["tpu7x-specifications", "system-architecture", "spec-table"]


@needs_corpus
def test_prolog_is_not_counted_as_content(tpu) -> None:  # type: ignore[no-untyped-def]
    for t in tpu.topics:
        assert "author" not in t.elements and "critdates" not in t.elements, t.path


@needs_corpus
def test_cli_writes_json_and_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "inv.json"
    assert main(["inventory", str(TPU_MAP), "--out", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["schema_version"] == "1" and data["totals"]["keyrefs"] == 169
    assert "25 topics" in capsys.readouterr().out


# ---------------------------------------------------------------- small fixtures: failure paths

MAP = """<?xml version="1.0"?>
<map><mapref href="keys.ditamap" processing-role="resource-only"/>
  <topicref href="a.dita" audience="admin"><topicref href="b.dita"/></topicref>
  <topicref href="w.dita" processing-role="resource-only"/>
  <reltable><relrow><relcell><topicref href="a.dita"/></relcell></relrow></reltable>
</map>"""
KEYS = '<map><keydef keys="speed"><topicmeta><keywords><keyword>400</keyword></keywords></topicmeta></keydef></map>'
A = """<topic id="a"><title>A</title><prolog><author>x</author></prolog><body>
  <p id="p1">Speed is <keyword keyref="speed"/> and <keyword keyref="missing"/>.</p>
  <note conref="w.dita#w/n1"/><note conref="w.dita#w/nope"/>
  <p><xref href="b.dita#b"/> <xref href="b.dita#b/gone"/> <xref href="https://example.com" scope="external"/></p>
</body></topic>"""
B = '<topic id="b"><title>B</title><body><p platform="v5e v6e">Only some.</p></body></topic>'
W = '<topic id="w"><title>W</title><body><note id="n1">Shared.</note></body></topic>'


@pytest.fixture()
def tiny(tmp_path: Path) -> Path:
    for name, text in {"m.ditamap": MAP, "keys.ditamap": KEYS, "a.dita": A, "b.dita": B, "w.dita": W}.items():
        (tmp_path / name).write_text(text)
    return tmp_path / "m.ditamap"


def test_fixture_counts_and_failures(tiny: Path) -> None:
    inv = inventory_map(tiny)
    a, b, w = inv.topics
    assert (a.path, b.path, w.path) == ("a.dita", "b.dita", "w.dita")
    assert inv.reltable_rows == 1 and inv.keys == {"speed": "400"}
    assert [(k.key, k.defined, k.value) for k in a.keyrefs] == [
        ("speed", True, "400"),
        ("missing", False, None),
    ]
    assert [c.resolves for c in a.conrefs] == [True, False]
    assert [(lk.scope, lk.resolves) for lk in a.links] == [
        ("local", True),
        ("local", False),
        ("external", None),
    ]
    assert a.ids == ["a", "p1"] and "author" not in a.elements
    assert b.map_conditions == {"audience": ["admin"]}  # cascaded from the parent topicref
    assert [(c.attribute, c.values) for c in b.conditions] == [("platform", ["v5e", "v6e"])]
    assert w.processing_role == "resource-only"
    assert a.keyrefs[1].line == 2


def test_cli_fails_on_broken_reuse(tiny: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["inventory", str(tiny), "--out", str(tmp_path / "o.json")]) == 1
    out = capsys.readouterr().out
    assert "conref does not resolve: a.dita -> w.dita#w/nope" in out
    assert "key is not defined: a.dita -> missing" in out
