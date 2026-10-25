"""The Cloud Run job (build step 4, ADR 0008), run on the real corpus with a memory store and a
local folder in place of Firestore and Cloud Storage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import FIXTURE_RULES, ROOT, needs_toolchain

from manifest.cloud import JobError, list_maps, resolve_map, run_job
from manifest.store import LocalBlobs, MemoryStore

CORPUS = ROOT / "corpus"
TOPIC = "topics/tpu-v5e-slice-planning.dita"


def test_the_job_runs_only_maps_in_the_corpus(tmp_path: Path) -> None:
    assert "corpus/tpu/tpu.ditamap" in list_maps(CORPUS)
    assert resolve_map("corpus/tpu/tpu.ditamap", CORPUS) == (CORPUS / "tpu" / "tpu.ditamap").resolve()
    (tmp_path / "x.ditamap").write_text("<map/>")
    for bad in (
        "corpus/tpu/tpu.dita",
        "corpus/../pyproject.toml",
        str(tmp_path / "x.ditamap"),
        "corpus/nope.ditamap",
    ):
        with pytest.raises(JobError):
            resolve_map(bad, CORPUS)


def test_a_bad_setting_fails_the_run_and_says_why(tmp_path: Path) -> None:
    store = MemoryStore()
    with pytest.raises(JobError):
        run_job(
            store,
            LocalBlobs(tmp_path / "b"),
            {"MANIFEST_RUN_ID": "r1", "MANIFEST_MODE": "guess"},
            tmp_path,
            CORPUS,
        )
    run = store.get("runs", "r1")
    assert run["status"] == "failed" and "unknown mode" in run["error"]
    with pytest.raises(JobError, match="no recording"):
        run_job(
            store,
            LocalBlobs(tmp_path / "b"),
            {"MANIFEST_RUN_ID": "r2", "MANIFEST_MODE": "replay"},
            tmp_path / "w2",
            CORPUS,
        )
    assert store.get("runs", "r2")["status"] == "failed"


@pytest.fixture(scope="module")
def migrated_in_cloud(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, Any], MemoryStore, Path]:
    """migrate mode, with the fixture rules approved in the console (Firestore), not in the repo."""
    tmp = tmp_path_factory.mktemp("job")
    store = MemoryStore()
    for p in sorted(FIXTURE_RULES.glob("*.json")):
        rule = json.loads(p.read_text())
        store.put("rules", rule["id"], rule, "writer@example.com", "rule.approve")
    store.put("runs", "r1", {"status": "queued"}, "writer@example.com", "run.queue")
    env = {"MANIFEST_RUN_ID": "r1", "MANIFEST_MODE": "migrate", "MANIFEST_TRIGGERED_BY": "writer@example.com"}
    result = run_job(
        store, LocalBlobs(tmp / "bucket"), env, tmp / "work", CORPUS, repo_rules=tmp / "no-rules"
    )
    return result, store, tmp / "bucket"


@needs_toolchain
def test_a_migrate_run_records_its_result(
    migrated_in_cloud: tuple[dict[str, Any], MemoryStore, Path],
) -> None:
    result, store, _ = migrated_in_cloud
    run = store.get("runs", "r1")
    assert run["status"] == "succeeded" and run["gate"] == "pass" and run["model"] == "migrate"
    assert run["summary"]["passed"] == 23 and run["summary"]["review"] == 1, (
        "approved rules from Firestore ran"
    )
    assert run["triggered_by"] == "writer@example.com" and run["prefix"] == "runs/r1"
    assert next(m for m in run["metrics"] if m["metric"] == "Fixes approved")["measured"] == "2 approved"
    assert [e["action"] for e in store.list("audit", limit=100) if e["target"] == "runs/r1"].count(
        "run.finish"
    ) == 1


@needs_toolchain
def test_the_run_stores_what_the_console_needs(
    migrated_in_cloud: tuple[dict[str, Any], MemoryStore, Path],
) -> None:
    _, _, bucket = migrated_in_cloud
    run = bucket / "runs" / "r1"
    for f in ("report/report.html", "report/report.json", "report/assets/fonts/IBMPlexSans.woff2",
              "inventory.json", "ledger.json", "myst/conf.py", "myst/topics/tpu-v5e-slice-planning.md"):  # fmt: skip
        assert (run / f).is_file(), f
    assert not list(run.rglob("_build")), "built sites stay out of storage"
    size = sum(p.stat().st_size for p in run.rglob("*") if p.is_file())
    assert size < 5_000_000, f"a run stores {size} bytes; Cloud Storage's free 5 GB is shared by six products"


@needs_toolchain
def test_an_agent_run_proposes_rules_and_opens_reviews(tmp_path: Path) -> None:
    """Scripted model: one topic, two fixes that fail, so a review; nothing is approved by the job."""
    choice = json.loads((FIXTURE_RULES / "choicetable-to-simpletable.json").read_text())
    bad = {"call": "try_fix", "args": {"rule_id": "choicetable-only", "matches": "choicetable",
                                       "description": "Choice table only.", "xslt": choice["xslt"]}}  # fmt: skip
    script = [{"call": "read_failure", "args": {}}, bad, bad,
              {"call": "open_review", "args": {"summary": "Two elements need rules."}}, {"text": "Sent."}]  # fmt: skip
    store = MemoryStore()
    env = {"MANIFEST_RUN_ID": "r9", "MANIFEST_MODE": "scripted"}
    result = run_job(store, LocalBlobs(tmp_path / "bucket"), env, tmp_path / "work", CORPUS,
                     repo_rules=tmp_path / "none", script=script)  # fmt: skip
    assert result["gate"] == "fail" and result["review"] == {TOPIC: "agent/reviews/tpu-v5e-slice-planning.md"}
    (review,) = store.list("reviews")
    assert (
        review["topic"] == TOPIC
        and review["summary"] == "Two elements need rules."
        and len(review["attempts"]) == 2
    )
    assert (tmp_path / "bucket" / review["file"]).is_file()
    assert store.list("rules") == [], "a failed fix is never proposed"


@needs_toolchain
def test_a_passing_fix_is_proposed_and_a_writers_decision_stands(tmp_path: Path) -> None:
    choice = json.loads((FIXTURE_RULES / "choicetable-to-simpletable.json").read_text())
    hazard = json.loads((FIXTURE_RULES / "hazardstatement-to-simpletable.json").read_text())
    both = {"call": "try_fix", "args": {"rule_id": "slice-planning-tables", "matches": "choicetable hazardstatement",
                                        "description": "Both become simple tables.",
                                        "xslt": choice["xslt"] + "\n" + hazard["xslt"]}}  # fmt: skip
    store = MemoryStore()
    rejected = {"id": "old-rule", "match": "x", "description": "d", "xslt": "", "status": "rejected"}
    store.put("rules", "old-rule", rejected, "writer@example.com", "rule.reject")
    env = {"MANIFEST_RUN_ID": "r8", "MANIFEST_MODE": "scripted"}
    script = [{"call": "read_failure", "args": {}}, both, {"text": "Fixed."}]
    result = run_job(store, LocalBlobs(tmp_path / "bucket"), env, tmp_path / "work", CORPUS,
                     repo_rules=tmp_path / "none", script=script)  # fmt: skip
    assert result["gate"] == "pass" and result["fixed"][TOPIC]["rule"] == "slice-planning-tables"
    rule = store.get("rules", "slice-planning-tables")
    assert rule["status"] == "proposed" and rule["provenance"]["run"] == "r8", (
        "the job proposes; a writer approves"
    )
    assert store.get("rules", "old-rule")["status"] == "rejected"
    assert store.list("rules", status="approved") == []
