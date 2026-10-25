"""US-01 · Docs Platform Lead: one report per map that lists the topics that passed and failed and why.

Accepted when: every map has a report · every failure links to the topic and the check that
failed · no topic is skipped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from conftest import load, needs_toolchain

from manifest.report import build_report, render_html


@needs_toolchain
def test_every_map_has_a_report(migrated: tuple[dict[str, Any], Path]) -> None:
    report, out = migrated
    assert (out / "report" / "report.json").exists() and (out / "report" / "report.html").exists()
    assert load(out / "report" / "report.json")["map"] == "tpu.ditamap"
    assert (out / "report" / "assets" / "fonts" / "IBMPlexSans.woff2").exists()  # self-hosted, no font CDN


@needs_toolchain
def test_no_topic_is_skipped(migrated: tuple[dict[str, Any], Path]) -> None:
    report, out = migrated
    inventory = load(out / "inventory.json")
    published = {t["path"] for t in inventory["topics"] if t["processing_role"] == "normal"}
    assert {t["topic"] for t in report["topics"]} == published and len(published) == 24
    assert all(set(t["checks"]) == {"counts", "text", "ids", "links"} for t in report["topics"])


@needs_toolchain
def test_the_tpu_map_passes_with_one_topic_for_a_writer(migrated: tuple[dict[str, Any], Path]) -> None:
    report, _ = migrated
    assert report["gate"] == "pass"
    assert report["summary"] | {"builds": None} == {
        "topics": 24,
        "passed": 23,
        "fixed": 0,
        "review": 1,
        "failed": 0,
        "builds": None,
    }


@needs_toolchain
def test_without_a_rule_the_specialized_topic_fails_and_says_why(
    baseline: tuple[dict[str, Any], Path],
) -> None:
    report, _ = baseline
    assert report["gate"] == "fail" and report["summary"]["failed"] == 1
    row = next(t for t in report["topics"] if t["status"] == "fail")
    assert row["topic"] == "topics/tpu-v5e-slice-planning.dita"
    assert {d["check"] for d in row["differences"]} >= {"text", "structure", "ids"}
    assert any("tables: DITA-OT 2" in d["detail"] for d in row["differences"])


@needs_toolchain
def test_every_failure_names_its_topic_and_check(migrated: tuple[dict[str, Any], Path]) -> None:
    """Inject a failing check into this run's real results: the report must mark the topic failed,
    name the check and the build, and link the failure to the topic's row in the HTML."""
    _, out = migrated
    check = load(out / "check" / "check.json")
    target = next(t for t in check["topics"] if t["topic"] == "topics/tpu-networking.dita")
    target["builds"][0]["differences"].append(
        {
            "check": "text",
            "detail": "lost in migration after '…Choose the network'",
            "dita": "subnetwork",
            "myst": "",
        }
    )
    report = build_report(
        load(out / "inventory.json"), load(out / "convert" / "convert.json"),
        load(out / "myst" / "_manifest" / "restore.json"), check, load(out / "links" / "links.json"), {},
    )  # fmt: skip
    row = next(t for t in report["topics"] if t["topic"] == "topics/tpu-networking.dita")
    assert row["status"] == "fail" and row["checks"]["text"] == "fail" and report["gate"] == "fail"
    assert row["differences"][0]["build"] == "unfiltered" and row["differences"][0]["check"] == "text"
    page = render_html(report)
    assert 'id="topic-topics-tpu-networking-dita"' in page
    assert (
        '<a href="#topic-topics-tpu-networking-dita">topics/tpu-networking.dita</a> · <b>text</b> [unfiltered]'
        in page
    )
