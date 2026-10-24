"""Tool 3, restore_reuse: placeholders become MyST includes, substitutions, only blocks and anchors.

Covers US-02 (reuse kept), US-05 (old anchors resolve), US-06 (no typed copies of keyed values)
and the build half of US-07 (one Sphinx build per DITAVAL file, matching DITA-OT's choices).
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

from manifest.tools.convert import ConvertError, convert_map, find_dita
from manifest.tools.inventory import inventory_map
from manifest.tools.restore import (
    GUARD_OPEN,
    Build,
    RestoreResult,
    apply_conditions,
    apply_tokens,
    guard_links,
    include_name,
    load_builds,
    only_expression,
    restore_reuse,
    substitution_name,
)

ROOT = Path(__file__).resolve().parent.parent
TPU_MAP = ROOT / "corpus" / "tpu" / "tpu.ditamap"
try:
    DITA: str | None = find_dita()
except ConvertError:
    DITA = None
HAS_SPHINX = all(
    importlib.util.find_spec(m) for m in ("sphinx", "myst_parser", "sphinx_substitution_extensions")
)
needs_corpus = pytest.mark.skipif(DITA is None or not TPU_MAP.exists(), reason="needs DITA-OT and the corpus")
needs_sphinx = pytest.mark.skipif(not HAS_SPHINX, reason="needs the [sphinx] extra")


# ---------------------------------------------------------------- naming


def test_hyphenated_keys_become_identifiers() -> None:
    """Jinja2 reads {{ product-tpu7x }} as product minus tpu7x (found on Sphinx 9.2)."""
    assert substitution_name("product-tpu7x") == "product_tpu7x"
    assert substitution_name("2d-shape") == "k_2d_shape"


def test_include_files_are_named_after_the_shared_element() -> None:
    assert (
        include_name("reuse/tpu-warehouse.dita#tpu-warehouse/note-slice-shape")
        == "tpu-warehouse--note-slice-shape"
    )


# ---------------------------------------------------------------- DITAVAL semantics


@pytest.fixture()
def builds(tmp_path: Path) -> list[Build]:
    for name in ("v5e", "v6e"):
        (tmp_path / f"{name}.ditaval").write_text(
            f'<val><prop att="platform" val="{name}" action="include"/><prop att="platform" action="exclude"/></val>'
        )
    b, problems = load_builds(sorted(tmp_path.glob("*.ditaval")))
    assert not problems
    return b


def test_ditaval_visibility(builds: list[Build]) -> None:
    unfiltered, v5e, v6e = builds
    assert unfiltered.visible("platform", ["v5p"])
    assert v5e.visible("platform", ["v5e"]) and not v5e.visible("platform", ["v6e"])
    assert v5e.visible("platform", ["v5e", "v6e"]), "excluded only if every value is excluded"
    assert v5e.visible("audience", ["admin"]), "other attributes are untouched"


def test_only_expressions(builds: list[Build]) -> None:
    unfiltered, v5e, v6e = builds
    assert only_expression(builds, builds) is None
    assert only_expression([unfiltered, v5e], builds) == "v5e or not ditaval"
    assert only_expression([v6e], builds) == "v6e"


def test_unsupported_ditaval_actions_are_reported(tmp_path: Path) -> None:
    (tmp_path / "f.ditaval").write_text('<val><prop att="platform" val="x" action="flag"/></val>')
    _, problems = load_builds([tmp_path / "f.ditaval"])
    assert problems and "flag" in problems[0]


# ---------------------------------------------------------------- conditions


def _cond(tok: int, element: str, values: list[str]) -> dict:  # type: ignore[type-arg]
    return {
        "token": f"@@B{tok}@@",
        "kind": "condition",
        "element": element,
        "owner": "t.dita",
        "detail": {"attribute": "platform", "values": values},
    }


def test_conditional_steps_lift_the_list_and_renumber(builds: list[Build]) -> None:
    md = [
        "1.  Set the project.",
        "",
        "2.  @@B1@@Create v5e.@@E1@@",
        "",
        "3.  @@B2@@Create v6e.@@E2@@",
        "",
        "4.  Done.",
        "",
    ]
    out = "\n".join(
        apply_conditions(
            md,
            [_cond(1, "step", ["v5e"]), _cond(2, "step", ["v6e"])],
            builds,
            RestoreResult("", []),
            "t.dita",
        )
    )
    assert (
        "::::{only} not ditaval\n1.  Set the project.\n\n2.  Create v5e.\n\n3.  Create v6e.\n\n4.  Done.\n::::"
        in out
    )
    assert "::::{only} v5e\n1.  Set the project.\n\n2.  Create v5e.\n\n3.  Done.\n::::" in out
    assert "@@" not in out


def test_conditional_rows_lift_the_table(builds: list[Build]) -> None:
    md = ["|Gen|Ver|", "|---|---|", "|@@B1@@v5e|a@@E1@@|", "|@@B2@@v6e|b@@E2@@|", ""]
    out = "\n".join(
        apply_conditions(
            md, [_cond(1, "row", ["v5e"]), _cond(2, "row", ["v6e"])], builds, RestoreResult("", []), "t.dita"
        )
    )
    assert "::::{only} v6e\n|Gen|Ver|\n|---|---|\n|v6e|b|\n::::" in out


def test_conditional_block_is_wrapped(builds: list[Build]) -> None:
    md = ["Intro.", "", "**Note:** @@B1@@Only v6e.@@E1@@", ""]
    out = "\n".join(
        apply_conditions(md, [_cond(1, "note", ["v6e"])], builds, RestoreResult("", []), "t.dita")
    )
    assert "::::{only} v6e or not ditaval\n**Note:** Only v6e.\n::::" in out  # also in the unfiltered build


# ---------------------------------------------------------------- tokens


def _keys(**pairs: str) -> dict:  # type: ignore[type-arg]
    return {t: {"kind": "key", "detail": {"key": k}} for t, k in pairs.items()}


def test_keys_in_code_blocks_and_inline_code() -> None:
    by = _keys(**{"@@K1@@": "v5e-runtime", "@@K2@@": "v5e-runtime", "@@K3@@": "product-v5e"})
    md = ["Use @@K3@@ with `--version=@@K2@@`.", "", "    ```bash", "    run --version=@@K1@@", "    ```"]
    out = "\n".join(apply_tokens(md, "t.dita", "t", by, {}, {}, RestoreResult("", [])))
    assert "Use {{ product_v5e }} with {substitution-code}`--version={{v5e_runtime}}`." in out
    assert "    ```{code-block} bash\n    :substitutions:\n    run --version={{v5e_runtime}}\n    ```" in out


def test_block_conref_in_a_list_item_and_after_a_note_label() -> None:
    by = {
        "@@C1@@": {"kind": "conref", "detail": {"normalised": "w.dita#w/step"}},
        "@@C2@@": {"kind": "conref", "detail": {"normalised": "w.dita#w/note"}},
    }
    rel = {"w.dita#w/step": "../_includes/w--step.md", "w.dita#w/note": "../_includes/w--note.md"}
    md = ["1.  @@C1@@", "", "    **Note:** @@C2@@"]
    out = apply_tokens(md, "t.dita", "t", by, rel, {}, RestoreResult("", []))
    assert out == [
        "1.  ```{include} ../_includes/w--step.md",
        "    ```",
        "",
        "    ```{include} ../_includes/w--note.md",
        "    ```",
    ]


def test_ids_keep_dita_ot_anchor_form_including_on_images_and_headings() -> None:
    by = {
        "@@I1@@": {"kind": "id", "detail": {"id": "spec-table"}},
        "@@I2@@": {"kind": "id", "detail": {"id": "fig"}},
    }
    md = ["# Specs {#specs .reference}", "## Arch {#arch .section}", "|@@I1@@Spec|", "![@@I2@@Alt](a.png)"]
    out = apply_tokens(md, "t.dita", "specs", by, {}, {}, RestoreResult("", []))
    assert out[:2] == ["(specs)=", "# Specs"]
    assert out[2:6] == ['<span id="specs__arch"></span>', "", "(specs__arch)=", "## Arch"]
    assert out[6] == '|<span id="specs__spec-table"></span>Spec|'
    assert out[7] == '<span id="specs__fig"></span>![Alt](a.png)'


def test_leftover_tokens_are_a_hard_error() -> None:
    from manifest.tools.restore import RestoreError

    with pytest.raises(KeyError):
        apply_tokens(["@@K9@@"], "t.dita", "t", {}, {}, {}, RestoreResult("", []))
    with pytest.raises(RestoreError):
        apply_tokens(["```", "x"], "t.dita", "t", {}, {}, {}, RestoreResult("", []))


def test_generated_links_to_unpublished_pages_are_guarded(builds: list[Build]) -> None:
    by_build = {b.name: b for b in builds}
    md = ["-   **[Memory](../topics/m.md)**  ", "How memory works.", "", "[Specs](../topics/s.md)", ""]

    def rule(target: str) -> list[str]:  # m.md exists only in two builds; everything else in all
        return ["unfiltered", "v6e"] if target == "topics/m.md" else [b.name for b in builds]

    out = guard_links(
        md,
        "topics/a.md",
        rule,
        builds,
        by_build,
        RestoreResult("", []),
        "a",
    )
    assert out[0] == GUARD_OPEN + "unfiltered,v6e" and out[3] == "% manifest:end"
    assert out[4:] == ["", "[Specs](../topics/s.md)", ""]


# ---------------------------------------------------------------- the real corpus


@pytest.fixture(scope="module")
def restored(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    work = tmp_path_factory.mktemp("restore")
    convert_map(TPU_MAP, work / "convert", DITA)
    res = restore_reuse(TPU_MAP, work / "convert", work / "myst")
    return res, work / "myst"


@needs_corpus
def test_every_reuse_feature_is_mapped(restored) -> None:  # type: ignore[no-untyped-def]
    res, _ = restored
    t = res.totals()
    inv = inventory_map(TPU_MAP)
    published = [tp for tp in inv.topics if tp.processing_role == "normal"]
    assert sum(len(tp.keyrefs) for tp in published) == 166
    assert t["keys"] == 169  # the 166 in topics + 3 inside shared content (include files)
    assert t["conrefs_as_includes"] + t["conrefs_as_substitutions"] == 11  # US-02
    assert t["include_files"] == 5 and t["conrefs_as_substitutions"] == 1
    assert t["conditions"] == 11
    assert t["ids"] + t["ids_dropped"] == 12


@needs_corpus
def test_substitution_values_are_the_key_values(restored) -> None:  # type: ignore[no-untyped-def]
    res, _ = restored
    keys = inventory_map(TPU_MAP).keys
    for key, value in keys.items():
        assert res.substitutions[substitution_name(key)] == value


@needs_corpus
def test_migration_adds_no_typed_copies_of_keyed_values(restored) -> None:  # type: ignore[no-untyped-def]
    """US-06: every keyed value in the output comes from a substitution. Text that already equals a
    key's value in the DITA source is kept, and each occurrence is reported for a writer."""
    res, out = restored
    values = {v for v in inventory_map(TPU_MAP).keys.values() if len(v) >= 3 and re.search(r"\d", v)}
    text = "\n".join(p.read_text() for p in out.rglob("*.md"))
    in_output = {v: len(re.findall(rf"(?<![\w.-]){re.escape(v)}(?![\w.-])", text)) for v in values}
    in_source: dict[str, int] = {}
    for f in res.findings:
        if f["kind"] == "possible-typed-copy":
            in_source[f["value"]] = in_source.get(f["value"], 0) + 1
    assert {v: n for v, n in in_output.items() if n} == in_source
    assert sum(in_source.values()) == 19


@needs_corpus
def test_reltable_leak_is_reported_as_a_finding(restored) -> None:  # type: ignore[no-untyped-def]
    res, _ = restored
    leaks = [f for f in res.findings if f["kind"] == "reltable-keeps-filtered-topic"]
    assert len(leaks) == 16  # 4 generations x 4 topics the reltable keeps published


@needs_corpus
@needs_sphinx
@pytest.mark.parametrize("build", ["unfiltered", "v5e", "v5p", "v6e", "tpu7x"])
def test_every_build_has_zero_sphinx_warnings(restored, build: str, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    _, out = restored
    tags = [] if build == "unfiltered" else ["-t", "ditaval", "-t", build]
    proc = subprocess.run(
        [sys.executable, "-m", "sphinx", "-b", "html", "-W", "-q", *tags, str(out), str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    pages = len(list((tmp_path / "topics").glob("*.html")))
    assert pages == (23 if build in ("unfiltered", "tpu7x") else 22)  # what DITA-OT 4.4 publishes


@needs_corpus
@needs_sphinx
def test_every_published_id_is_an_anchor_in_dita_ot_form(restored, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """US-05: page.html#topicid__elemid keeps working after the move."""
    _, out = restored
    subprocess.run([sys.executable, "-m", "sphinx", "-b", "html", "-q", str(out), str(tmp_path)], check=True)
    missing = []
    for tp in inventory_map(TPU_MAP).topics:
        if tp.processing_role != "normal":
            continue
        ids = set(re.findall(r'id="([^"]+)"', (tmp_path / Path(tp.path).with_suffix(".html")).read_text()))
        missing += [a for i in tp.ids if (a := i if i == tp.topic_id else f"{tp.topic_id}__{i}") not in ids]
    assert missing == []
