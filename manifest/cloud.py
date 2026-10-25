"""The Cloud Run job: one migration, its results stored where the API and console can read them.

Started by the API (`POST /runs`) with environment overrides, or by hand with
`gcloud run jobs execute manifest-migrate`. Settings, all optional:

    MANIFEST_RUN_ID        the run's ID (the API creates the run document first)
    MANIFEST_MAP           a .ditamap under corpus/ (default corpus/tpu/tpu.ditamap)
    MANIFEST_MODE          migrate (approved rules, no agent), replay (a committed recording) or
                           live (Gemini; the key arrives as GOOGLE_API_KEY from Secret Manager)
    MANIFEST_MODEL         Gemini model for live mode
    MANIFEST_TRIGGERED_BY  who asked for the run, for the audit trail
    MANIFEST_BUCKET        the report bucket (set at deploy time)

A failing gate is a result, not a job failure: the job exits 0 and the run records the gate. The
job exits non-zero only if the run couldn't complete, and then the run document says why.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from manifest.agent import DEFAULT_MODEL, check_api_key, redact
from manifest.pipeline import migrate
from manifest.rules import Rule, RuleSet, load_rules, save_rule
from manifest.store import Blobs, NotFound, Store, now

# The checkout (tests, a laptop) or /app in the image, where the package lives in site-packages.
ROOT = Path(os.environ.get("MANIFEST_ROOT") or Path(__file__).resolve().parent.parent)
CORPUS = ROOT / "corpus"
MODES = ("migrate", "replay", "live")
PUBLIC_MODES = ("migrate", "replay")  # no model call, no quota: anyone may start these (ADR 0010)
ACTOR = "manifest-job"


class JobError(ValueError):
    pass


def new_run_id() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def _map_roots(corpus: Path) -> list[Path]:
    return [corpus.resolve(), (corpus.parent / "help").resolve()]


def resolve_map(value: str, corpus: Path = CORPUS) -> Path:
    """A map the job may run: a root .ditamap in the corpus or the help set, nothing else."""
    p = (corpus.parent / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    allowed = p.suffix == ".ditamap" and p.is_file() and any(p.is_relative_to(r) for r in _map_roots(corpus))
    if not allowed or str(p.relative_to(corpus.parent.resolve())) not in list_maps(corpus):
        raise JobError(f"not a DITA map in the corpus: {value}")
    return p


def recording_for(map_path: Path, root: Path = ROOT) -> Path:
    return root / "recordings" / f"{map_path.stem}.jsonl"


def list_maps(corpus: Path = CORPUS) -> list[str]:
    """Root maps a user can migrate: one folder down in the corpus, plus MANIFEST's own help.
    Key maps, subject schemes and other maps that other maps include are not offered."""
    base = corpus.parent.resolve()
    found = [*corpus.resolve().glob("*/*.ditamap"), *(base / "help").glob("*.ditamap")]
    roots = [p for p in found if b"subjectScheme" not in p.read_bytes()[:600]]
    return sorted(str(p.relative_to(base)) for p in roots)


def available_modes(map_value: str, corpus: Path = CORPUS) -> list[str]:
    """Modes that can run for this map: replay needs a recording of a live run."""
    rec = corpus.parent / "recordings" / f"{Path(map_value).stem}.jsonl"
    return [m for m in MODES if m != "replay" or rec.exists()]


def _approved_rules(store: Store, repo_rules: Path, dest: Path) -> list[Rule]:
    """Approved rules from the repo (reviewed in Git) and from Firestore (approved in the console)."""
    rules = {r.id: r for r in load_rules(repo_rules)}
    for doc in store.list("rules", limit=500, status="approved"):
        rules.setdefault(doc["id"], Rule.from_dict(doc))
    for r in rules.values():
        save_rule(r, dest)
    return list(rules.values())


def _stage(out: Path, stage: Path) -> None:
    """What the console needs, not the full builds: report, agent record, MyST pages, ledger."""
    keep = [
        ("report", "report"),
        ("myst", "myst"),
        ("inventory.json", "inventory.json"),
        ("convert/ledger.json", "ledger.json"),
        ("agent/run.json", "agent/run.json"),
        ("agent/proposed", "agent/proposed"),
        ("agent/reviews", "agent/reviews"),
        ("agent/recording.jsonl", "agent/recording.jsonl"),
    ]
    for src, dst in keep:
        s, d = out / src, stage / dst
        if s.is_dir():
            shutil.copytree(s, d, ignore=shutil.ignore_patterns("_build", "__pycache__"))
        elif s.is_file():
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)


def run_job(
    store: Store,
    blobs: Blobs,
    env: Mapping[str, str],
    work: Path,
    corpus: Path = CORPUS,
    repo_rules: Path = ROOT / "rules",
    script: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    run_id = env.get("MANIFEST_RUN_ID") or new_run_id()
    mode = env.get("MANIFEST_MODE", "migrate")
    model = env.get("MANIFEST_MODEL", DEFAULT_MODEL)
    by = env.get("MANIFEST_TRIGGERED_BY", "gcloud")
    map_value = env.get("MANIFEST_MAP", "corpus/tpu/tpu.ditamap")
    started = time.monotonic()
    store.merge(
        "runs",
        run_id,
        {"status": "running", "started_at": now(), "mode": mode, "map": map_value, "triggered_by": by},
        ACTOR,
        "run.start",
    )
    try:
        if mode not in MODES and not (mode == "scripted" and script is not None):
            raise JobError(f"unknown mode {mode!r}: use {', '.join(MODES)}")

        def show_stage(name: str) -> None:
            store.merge("runs", run_id, {"stage": name}, ACTOR, "run.progress")

        map_path = resolve_map(map_value, corpus)
        if mode == "live":
            check_api_key(env)  # before minutes of migration, not after
        rules_dir = work / "rules"
        approved = _approved_rules(store, repo_rules, rules_dir)
        out = work / "out"
        agent: dict[str, Any] = {}
        if mode == "migrate":
            report = migrate(
                map_path, out, rules=RuleSet(approved) if approved else None, progress=show_stage
            )
        else:
            from manifest.agent.runner import fix_map  # needs the [agent] extra

            recording = recording_for(map_path, corpus.parent) if mode == "replay" else None
            if mode == "replay" and not (recording and recording.exists()):
                raise JobError(f"no recording for {map_path.name}: record a live run first")
            if mode == "live":
                recording = out / "agent" / "recording.jsonl"
            agent = fix_map(
                map_path, out, mode, model, recording, rules_dir, script, run_id=run_id, progress=show_stage
            )
            report = json.loads((out / "report" / "report.json").read_text(encoding="utf-8"))

        stage = work / "stage"
        _stage(out, stage)
        files = blobs.upload_dir(stage, f"runs/{run_id}")

        for path in sorted((out / "agent" / "proposed").glob("*.json")):
            rule = Rule.from_dict(json.loads(path.read_text(encoding="utf-8")))
            try:
                existing = store.get("rules", rule.id)
            except NotFound:
                existing = {}
            if existing.get("status") in ("approved", "rejected"):
                continue  # a writer has decided; a later proposal doesn't reopen it
            store.put("rules", rule.id, json.loads(rule.to_json()), ACTOR, "rule.propose")
        for topic, info in agent.get("review", {}).items():
            session = next(s for s in agent["sessions"] if s["topic"] == topic)
            store.put(
                "reviews",
                f"{run_id}--{Path(topic).stem}",
                {
                    "run": run_id,
                    "topic": topic,
                    "status": "open",
                    "file": f"runs/{run_id}/{info['file']}",
                    "summary": (session.get("review") or {}).get("summary", ""),
                    "attempts": session["attempts"],
                },
                ACTOR,
                "review.open",
            )

        result = {
            "status": "succeeded",
            "stage": "done",
            "finished_at": now(),
            "duration_s": round(time.monotonic() - started),
            "model": model if mode == "live" else mode,
            "gate": report["gate"],
            "summary": report["summary"],
            "metrics": report["metrics"],
            "versions": report["versions"],
            "fixed": agent.get("fixed", {}),
            "review": {t: i["file"] for t, i in agent.get("review", {}).items()},
            "prefix": f"runs/{run_id}",
            "files": len(files),
        }
        store.merge("runs", run_id, result, ACTOR, "run.finish")
        return {"id": run_id, **result}
    except Exception as exc:
        store.merge(
            "runs",
            run_id,
            {
                "status": "failed",
                "finished_at": now(),
                "error": redact(str(exc), env)[:1000],
                "duration_s": round(time.monotonic() - started),
            },
            ACTOR,
            "run.fail",
        )
        raise


def main() -> int:  # pragma: no cover - the container entrypoint, exercised by deploy.yml's smoke run
    from manifest.store import FirestoreStore, GcsBlobs

    bucket = os.environ.get("MANIFEST_BUCKET")
    if not bucket:
        print("error: MANIFEST_BUCKET is not set", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        try:
            r = run_job(FirestoreStore(), GcsBlobs(bucket), os.environ, Path(tmp))
        except Exception as exc:
            print(f"error: {redact(str(exc), os.environ)}", file=sys.stderr)
            return 1
    print(f"run {r['id']}: gate {r['gate']}, {r['summary']}, {r['duration_s']} s, {r['files']} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
