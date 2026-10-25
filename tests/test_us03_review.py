"""US-03 · Information Architect: elements with no Markdown equivalent are sent to a writer.

Accepted when: each unmapped element creates a review item · zero elements are downgraded
without review.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from conftest import load, needs_toolchain


@needs_toolchain
def test_each_unmapped_element_creates_a_review_item(migrated: tuple[dict[str, Any], Path]) -> None:
    report, out = migrated
    ledger = load(out / "convert" / "convert.json")["ledger"]
    unhandled = [(e["owner"], e["element"], e["line"]) for e in ledger if e["kind"] == "unhandled"]
    items = [(i["topic"], i["element"], i["line"]) for i in report["review_items"]]
    assert unhandled and set(unhandled) <= set(items)


@needs_toolchain
def test_the_video_object_goes_to_a_writer(migrated: tuple[dict[str, Any], Path]) -> None:
    """Plain DITA-OT drops <object> from Markdown without a warning; MANIFEST names it."""
    report, _ = migrated
    assert report["review_items"] == [
        {"topic": "topics/osfp-transceiver.dita", "element": "object", "line": 38,
         "reason": "<object> has no Markdown equivalent; DITA-OT drops it"}
    ]  # fmt: skip
    row = next(t for t in report["topics"] if t["topic"] == "topics/osfp-transceiver.dita")
    assert row["status"] == "review" and "line 38" in row["note"]


@needs_toolchain
def test_an_id_with_no_place_to_go_is_a_review_item_until_a_rule_exists(
    baseline: tuple[dict[str, Any], Path],
) -> None:
    """Without a rule, the choice table's ID has no text to carry an anchor: a writer is told."""
    report, _ = baseline
    items = {(i["topic"], i["element"]) for i in report["review_items"]}
    assert ("topics/tpu-v5e-slice-planning.dita", "choicetable") in items


@needs_toolchain
def test_review_items_never_pass_silently(migrated: tuple[dict[str, Any], Path]) -> None:
    report, _ = migrated
    flagged = {i["topic"] for i in report["review_items"]}
    assert all(t["status"] != "pass" for t in report["topics"] if t["topic"] in flagged)
