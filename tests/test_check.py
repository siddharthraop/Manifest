"""Tool 4, check_topic: the migrated site says what DITA-OT says, topic by topic, in every build.

US-07: 'the text for each audience matches DITA-OT's output for the same DITAVAL file'.
The mutation tests prove the check is not lenient: each kind of loss is caught, by name.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
from pathlib import Path

import pytest
from lxml import html

from manifest.tools.check import check_topics, compare_text, text_of
from manifest.tools.convert import ConvertError, convert_map, find_dita
from manifest.tools.restore import restore_reuse

ROOT = Path(__file__).resolve().parent.parent
TPU_MAP = ROOT / "corpus" / "tpu" / "tpu.ditamap"
try:
    DITA: str | None = find_dita()
except ConvertError:
    DITA = None
HAS_SPHINX = all(
    importlib.util.find_spec(m) for m in ("sphinx", "myst_parser", "sphinx_substitution_extensions")
)
needs_all = pytest.mark.skipif(
    DITA is None or not HAS_SPHINX or not TPU_MAP.exists(),
    reason="needs DITA-OT, the [sphinx] extra and the corpus",
)


# ---------------------------------------------------------------- text extraction


def test_inline_elements_do_not_split_words_but_blocks_do() -> None:
    el = html.fromstring(
        "<div><p>Use <span>TPU v5e</span>, then <code><span>--zone</span>=ZONE</code>.</p><p>Next</p></div>"
    )
    assert text_of(el) == ["Use", "TPU", "v5e,", "then", "--zone=ZONE.", "Next"]


def test_differences_are_named_by_kind() -> None:
    a, b = ["one", "two", "three", "four"], ["one", "2", "four", "five"]
    kinds = [d.detail.split(" after")[0] for d in compare_text(a, b)]
    assert kinds == ["changed", "added in migration"]
    assert compare_text(["a", "b"], ["a"])[0].detail.startswith("lost in migration")


# ---------------------------------------------------------------- the real corpus, all builds


@pytest.fixture(scope="module")
def checked(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    work = tmp_path_factory.mktemp("check")
    convert_map(TPU_MAP, work / "convert", DITA)
    restore_reuse(TPU_MAP, work / "convert", work / "myst")
    res = check_topics(TPU_MAP, work / "myst", work / "check", DITA)
    return res, work


@needs_all
def test_us07_every_topic_matches_dita_ot_except_the_one_with_no_rule(checked) -> None:  # type: ignore[no-untyped-def]
    """With no mapping rules, only the deliberately specialized topic fails, and for the right
    reasons: the hazard statement and the choice table don't render as DITA-OT renders them."""
    res, _ = checked
    failing = {t.topic for t in res.topics if not t.ok}
    assert failing == {"topics/tpu-v5e-slice-planning.dita"}
    assert res.totals()["topics_passed"] == 23
    slice_topic = next(t for t in res.topics if t.topic == "topics/tpu-v5e-slice-planning.dita")
    assert slice_topic.failed_checks() == ["ids", "structure", "text"]
    assert [b.build for b in slice_topic.builds] == ["unfiltered", "v5e"]  # where DITA-OT publishes it


@needs_all
def test_us01_no_topic_is_skipped(checked) -> None:  # type: ignore[no-untyped-def]
    res, _ = checked
    t = res.totals()
    # unfiltered 24 · TPU7x 23 (no v5e slice planning) · v5e 23 · v5p and v6e 22 (no tpu7x-memory
    # and no v5e slice planning).
    assert t["comparisons"] == 24 + 23 + 23 + 22 + 22 == 114
    assert res.builds == ["unfiltered", "tpu7x", "v5e", "v5p", "v6e"]


@needs_all
def test_same_pages_published_as_dita_ot_in_every_build(checked) -> None:  # type: ignore[no-untyped-def]
    res, _ = checked
    assert all(not v for v in res.page_differences.values())


@needs_all
def test_sphinx_builds_are_warning_free(checked) -> None:  # type: ignore[no-untyped-def]
    res, _ = checked
    assert set(res.sphinx_warnings.values()) == {0}


@needs_all
def test_report_is_written(checked) -> None:  # type: ignore[no-untyped-def]
    _, work = checked
    data = json.loads((work / "check" / "check.json").read_text())
    assert data["ok"] is False and data["totals"]["topics_passed"] == 23 and data["totals"]["differences"] > 0


# ---------------------------------------------------------------- mutations: each loss is caught


def _mutated(work: Path, tmp_path: Path, edit) -> list:  # type: ignore[no-untyped-def,type-arg]
    """Copy the migrated site, break it, and check the unfiltered build again (reusing DITA-OT's HTML)."""
    myst = tmp_path / "myst"
    shutil.copytree(work / "myst", myst)
    shutil.copytree(work / "check" / "dita-html", tmp_path / "check" / "dita-html")
    edit(myst)
    res = check_topics(
        TPU_MAP, myst, tmp_path / "check", DITA, only_builds=["unfiltered"], reuse_dita_html=True
    )
    diffs = [(t.topic, d.check, d.detail) for t in res.topics for b in t.builds for d in b.differences]
    diffs += [("(pages)", d.check, d.detail) for v in res.page_differences.values() for d in v]
    return diffs


def _sub(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not in {path}"
    path.write_text(text.replace(old, new, 1))


@needs_all
def test_catches_a_deleted_sentence(checked, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    _, work = checked
    sentence = " Specify a custom network and subnetwork when you create the VM to place it in your own VPC."
    diffs = _mutated(work, tmp_path, lambda m: _sub(m / "topics/tpu-networking.md", sentence, ""))
    assert any(
        t == "topics/tpu-networking.dita" and c == "text" and d.startswith("lost in migration")
        for t, c, d in diffs
    )


@needs_all
def test_catches_a_wrong_spec_value(checked, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """A substitution changed in one place changes every page that uses it, and the check sees it."""
    _, work = checked

    def edit(m: Path) -> None:
        subs = m / "_manifest" / "substitutions.json"
        data = json.loads(subs.read_text())
        data["v5e_peak_bf16"] = "198"
        subs.write_text(json.dumps(data))

    diffs = _mutated(work, tmp_path, edit)
    hit = {t for t, c, d in diffs if c == "text" and "changed" in d}
    assert "topics/tpu-v5e-specifications.dita" in hit


@needs_all
def test_catches_a_missing_anchor(checked, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    _, work = checked
    diffs = _mutated(
        work,
        tmp_path,
        lambda m: _sub(
            m / "topics/tpu7x-specifications.md", '<span id="tpu7x-specifications__spec-table"></span>', ""
        ),
    )
    assert (
        "topics/tpu7x-specifications.dita",
        "ids",
        "1 anchor(s) missing: tpu7x-specifications__spec-table",
    ) in diffs


@needs_all
def test_catches_a_dropped_table_row(checked, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    _, work = checked

    def edit(m: Path) -> None:
        p = m / "topics/tpu7x-specifications.md"
        lines = p.read_text().split("\n")
        row = next(i for i, ln in enumerate(lines) if ln.startswith("|SparseCores"))
        p.write_text("\n".join(lines[:row] + lines[row + 1 :]))

    diffs = _mutated(work, tmp_path, edit)
    checks = {(c, d.split(":")[0]) for t, c, d in diffs if t == "topics/tpu7x-specifications.dita"}
    assert ("structure", "table rows") in checks and any(c == "text" for c, _ in checks)


@needs_all
def test_catches_a_missing_page(checked, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    _, work = checked
    diffs = _mutated(work, tmp_path, lambda m: (m / "topics/tpu-networking.md").unlink())
    assert any(c == "pages" and "tpu-networking" in d for _, c, d in diffs)


@needs_all
def test_catches_a_lost_figure_caption(checked, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """Regression: DITA-OT's Markdown writer keeps a figure title only as a tooltip."""
    _, work = checked

    def edit(m: Path) -> None:
        p = m / "topics/osfp-transceiver.md"
        p.write_text(re.sub(r"\{\.manifest-figure-caption\}\n[^\n]+\n", "", p.read_text()))

    diffs = _mutated(work, tmp_path, edit)
    assert any(t == "topics/osfp-transceiver.dita" and c == "text" and "lost" in d for t, c, d in diffs)


# ---------------------------------------------------------------- checker fixes found in step 3


def test_svg_icons_are_not_text(tmp_path: Path) -> None:
    """DITA-OT HTML5 inlines an SVG icon in each hazard statement; its metadata isn't text."""
    from manifest.tools.check import _content

    page = tmp_path / "p.html"
    page.write_text('<html><body><article><th><svg><title>image/svg+xml</title></svg> CAUTION</th>'
                    "<p>Check quota.</p></article></body></html>")  # fmt: skip
    assert text_of(_content(page, "dita")) == ["CAUTION", "Check", "quota."]


def test_expected_anchors_come_from_source_ids(tmp_path: Path) -> None:
    """IDs DITA-OT generates itself (property headers, table cells) are not source anchors."""
    from manifest.tools.check import source_anchors

    topic = tmp_path / "t.dita"
    topic.write_text('<reference id="r"><title>T</title><prolog><data id="meta"/></prolog><refbody>'
                     '<properties id="props"><property><proptype>A</proptype></property></properties>'
                     "</refbody></reference>")  # fmt: skip
    assert source_anchors(topic) == {"r", "r__props"}
