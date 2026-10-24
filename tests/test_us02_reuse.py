"""US-02 · Senior Technical Writer: every conref and key arrives as an include or a substitution.

Accepted when: includes in the output equal conrefs in the source · substitutions equal key
references · any difference fails the topic.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from conftest import load, needs_toolchain


@needs_toolchain
def test_includes_equal_conrefs(migrated: tuple[dict[str, Any], Path]) -> None:
    _, out = migrated
    inventory, restore = load(out / "inventory.json"), load(out / "myst" / "_manifest" / "restore.json")
    conrefs = sum(t["totals"]["conrefs"] for t in inventory["topics"] if t["processing_role"] == "normal")
    t = restore["totals"]
    assert t["conrefs_as_includes"] + t["conrefs_as_substitutions"] == conrefs == 12
    text = "\n".join(p.read_text() for p in (out / "myst" / "topics").glob("*.md"))
    assert len({m for m in re.findall(r"\{include\} (\S+)", text)}) == t["include_files"] == 5


@needs_toolchain
def test_substitutions_equal_key_references(migrated: tuple[dict[str, Any], Path]) -> None:
    _, out = migrated
    inventory, restore = load(out / "inventory.json"), load(out / "myst" / "_manifest" / "restore.json")
    keyrefs = sum(t["totals"]["keyrefs"] for t in inventory["topics"])  # topics + shared content
    assert restore["totals"]["keys"] == keyrefs == 171


@needs_toolchain
def test_a_value_changed_once_changes_everywhere(migrated: tuple[dict[str, Any], Path]) -> None:
    """The point of keeping reuse: the TPU7x name lives in one place and every page uses it."""
    _, out = migrated
    subs = load(out / "myst" / "_manifest" / "substitutions.json")
    assert subs["product_tpu7x"] == "TPU7x (Ironwood)"
    pages = [p for p in (out / "myst").rglob("*.md") if "{{ product_tpu7x }}" in p.read_text()]
    assert len(pages) >= 8
    assert not any("TPU7x (Ironwood)" in p.read_text() for p in (out / "myst").rglob("*.md"))
