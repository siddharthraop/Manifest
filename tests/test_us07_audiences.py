"""US-07 · Tomás (reader): only operator content for his build, and his bookmarks still work.

Accepted when: the text for each audience matches DITA-OT's output for the same DITAVAL file ·
old anchors still resolve.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from conftest import load, needs_toolchain


@needs_toolchain
def test_text_matches_dita_ot_for_every_ditaval(migrated: tuple[dict[str, Any], Path]) -> None:
    _, out = migrated
    check = load(out / "check" / "check.json")
    assert check["builds"] == ["unfiltered", "tpu7x", "v5e", "v5p", "v6e"]
    assert check["ok"] and check["totals"]["differences"] == 0 and check["totals"]["comparisons"] == 112


@needs_toolchain
def test_a_v5e_reader_sees_only_v5e_steps(migrated: tuple[dict[str, Any], Path]) -> None:
    _, out = migrated
    page = (out / "check" / "sphinx-html" / "v5e" / "topics" / "tpu-create-vm.html").read_text()
    assert "v2-alpha-tpuv5-lite" in page
    assert "v2-alpha-tpuv5<" not in page and "v2-alpha-tpuv6e" not in page and "tpu7x-standard-4t" not in page


@needs_toolchain
def test_old_anchors_resolve_in_every_build(migrated: tuple[dict[str, Any], Path]) -> None:
    _, out = migrated
    links = load(out / "links" / "links.json")
    assert sorted(links["builds"]) == ["tpu7x", "unfiltered", "v5e", "v5p", "v6e"]
    assert not [p for p in links["problems"] if p["check"] == "old-url"]
