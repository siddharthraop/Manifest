"""The agent (design doc §5): Gemini proposes a fix, the checks decide, two fixes at most, then a writer.

The model is scripted here, so the tests don't need a key. Everything else is real: the rules run
through DITA-OT, Sphinx and the checks on the corpus. One scripted run is recorded and replayed, which
is how CI and the public demo run the agent without calling Gemini.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("google.adk")

from conftest import FIXTURE_RULES, TPU_MAP, load, needs_toolchain  # noqa: E402

from manifest.agent.models import ModelError, ReplayLlm, make_model  # noqa: E402
from manifest.cli import main  # noqa: E402
from manifest.rules import Rule, RuleSet, load_rules  # noqa: E402

TOPIC = "topics/tpu-v5e-slice-planning.dita"
CHOICE = json.loads((FIXTURE_RULES / "choicetable-to-simpletable.json").read_text())
HAZARD = json.loads((FIXTURE_RULES / "hazardstatement-to-simpletable.json").read_text())


def fix_call(rule_id: str, matches: str, xslt: str) -> dict[str, Any]:
    desc = "Rewrites the element into a simple table with the same text and IDs."
    return {
        "call": "try_fix",
        "args": {"rule_id": rule_id, "matches": matches, "description": desc, "xslt": xslt},
    }


# The topic has a <choicetable> and a <hazardstatement>. A rule for one of them isn't enough.
CHOICE_ONLY = fix_call("choicetable-only", "choicetable", CHOICE["xslt"])
BOTH = fix_call(
    "slice-planning-tables", "choicetable hazardstatement", CHOICE["xslt"] + "\n" + HAZARD["xslt"]
)
UNSAFE = fix_call("reads-a-file", "choicetable",
                  '<xsl:template match="choicetable"><p><xsl:value-of select="document(\'/etc/passwd\')"/></p></xsl:template>')  # fmt: skip


@pytest.fixture(scope="session")
def fixed(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, Any], Path]:
    """`manifest fix` with a scripted model that fails once and then fixes the topic; recorded."""
    from manifest.agent.runner import fix_map

    out = tmp_path_factory.mktemp("fix")
    script = [{"call": "read_failure", "args": {}}, CHOICE_ONLY, BOTH, {"text": "Both become simple tables."}]
    run = fix_map(TPU_MAP, out, "scripted", script=script, recording=out / "recording.jsonl")
    return run, out


# ------------------------------------------------------------------ fixed, proposed, approved


@needs_toolchain
def test_a_failed_fix_is_followed_by_one_that_passes(fixed: tuple[dict[str, Any], Path]) -> None:
    run, _ = fixed
    assert run["failing_before"] == [TOPIC]
    (s,) = run["sessions"]
    first, second = s["attempts"]
    assert not first["passed"] and first["differences"], "the checks reject the choicetable-only rule"
    assert any(d["check"] == "structure" for d in first["differences"])
    assert second["passed"] and not second["differences"] and not second["other_topics_broken"]
    assert s["outcome"] == "fixed" and run["gate"] == "pass"


@needs_toolchain
def test_a_passing_fix_is_saved_as_a_proposed_rule_with_provenance(
    fixed: tuple[dict[str, Any], Path],
) -> None:
    run, out = fixed
    (path,) = (out / "agent" / "proposed").glob("*.json")
    rule = Rule.from_dict(load(path))
    assert rule.id == "slice-planning-tables" and rule.status == "proposed"
    assert rule.provenance["topic"] == TOPIC and rule.provenance["run"] == run["run"]
    assert rule.provenance["attempt"] == "2" and rule.provenance["proposed_by"] == "scripted"
    assert load_rules(out / "agent" / "proposed") == [], "a proposed rule doesn't run until approved"


@needs_toolchain
def test_the_report_marks_the_topic_fixed_until_a_writer_approves(fixed: tuple[dict[str, Any], Path]) -> None:
    _, out = fixed
    report = load(out / "report" / "report.json")
    row = next(t for t in report["topics"] if t["topic"] == TOPIC)
    assert row["status"] == "fixed" and "slice-planning-tables" in row["note"]
    assert report["summary"]["fixed"] == 1 and report["summary"]["failed"] == 0
    metric = next(m for m in report["metrics"] if m["metric"] == "Fixes approved")
    assert metric["measured"] == "0 approved, 1 proposed"
    assert "✎ Fixed" in (out / "report" / "report.html").read_text()


@needs_toolchain
def test_an_approved_rule_runs_without_the_agent(fixed: tuple[dict[str, Any], Path], tmp_path: Path) -> None:
    from manifest.pipeline import migrate

    _, out = fixed
    proposed = out / "agent" / "proposed" / "slice-planning-tables.json"
    assert (
        main(["rules", "approve", str(proposed), "--by", "A Writer", "--rules", str(tmp_path / "rules")]) == 0
    )
    (rule,) = load_rules(tmp_path / "rules")
    assert rule.status == "approved" and rule.provenance["approved_by"] == "A Writer"
    assert rule.provenance["topic"] == TOPIC, "approval keeps where the rule came from"
    report = migrate(TPU_MAP, tmp_path / "build", rules=RuleSet([rule]))
    row = next(t for t in report["topics"] if t["topic"] == TOPIC)
    assert row["status"] == "pass" and report["gate"] == "pass"
    assert next(m for m in report["metrics"] if m["metric"] == "Fixes approved")["measured"] == "1 approved"


# ------------------------------------------------------------------ the limits are code, not prompt


@needs_toolchain
def test_two_failed_fixes_go_to_a_writer_and_a_third_is_refused(
    baseline: tuple[dict[str, Any], Path], tmp_path: Path
) -> None:
    from manifest.agent.runner import _run_session, review_markdown
    from manifest.agent.session import FixSession

    _, base = baseline
    script = [{"call": "read_failure", "args": {}}, UNSAFE, CHOICE_ONLY, BOTH,
              {"call": "open_review", "args": {"summary": "A choice table and a hazard statement."}},
              {"text": "Sent to a writer."}]  # fmt: skip
    model, _ = make_model("scripted", script=script)
    session = FixSession(TPU_MAP, TOPIC, base, tmp_path, approved=[])
    asyncio.run(_run_session(session, model, None))

    unsafe, choice_only = session.attempts
    assert not unsafe.valid and "document" in unsafe.error, "an unsafe rule counts as a failed attempt"
    assert choice_only.valid and not choice_only.passed
    assert session.refused == 1, "the third try_fix is refused, even though it would have passed"
    assert session.outcome == "review" and session.review == {
        "summary": "A choice table and a hazard statement.",
        "by": "agent",
    }
    issue = review_markdown(session, "test-run")
    assert "## Attempt 1: `reads-a-file`" in issue and "## Attempt 2: `choicetable-only`" in issue
    assert "A choice table and a hazard statement." in issue and "structure" in issue


@needs_toolchain
def test_saying_it_works_does_not_make_it_pass(baseline: tuple[dict[str, Any], Path], tmp_path: Path) -> None:
    from manifest.agent.runner import _run_session
    from manifest.agent.session import FixSession

    _, base = baseline
    script = [CHOICE_ONLY, {"text": "Fixed! Every check passes now."}]
    model, _ = make_model("scripted", script=script)
    session = FixSession(TPU_MAP, TOPIC, base, tmp_path, approved=[])
    asyncio.run(_run_session(session, model, None))
    assert session.final_text.startswith("Fixed!") and session.outcome == "review"


# ------------------------------------------------------------------ record and replay


@needs_toolchain
def test_a_recording_replays_to_the_same_result(fixed: tuple[dict[str, Any], Path], tmp_path: Path) -> None:
    from manifest.agent.runner import fix_map

    run, out = fixed
    recording = out / "recording.jsonl"
    lines = [json.loads(x) for x in recording.read_text().splitlines()]
    assert len(lines) == 4 and all(x["model"] == "scripted" for x in lines)
    replay = fix_map(TPU_MAP, tmp_path, "replay", recording=recording)
    assert replay["fixed"] == run["fixed"] and replay["gate"] == "pass"
    assert [a["passed"] for a in replay["sessions"][0]["attempts"]] == [False, True]


def test_a_run_that_diverges_from_its_recording_stops(tmp_path: Path) -> None:
    rec = tmp_path / "r.jsonl"
    rec.write_text(
        '{"n": 1, "model": "x", "response": {"content": {"role": "model", "parts": [{"text": "hi"}]}}}\n'
    )
    llm = ReplayLlm.from_file(rec)

    async def calls() -> list[str]:
        texts = []
        for _ in range(2):
            async for r in llm.generate_content_async(None):  # type: ignore[arg-type]
                texts.append(r.content.parts[0].text)  # type: ignore[union-attr,index]
        return texts

    with pytest.raises(ModelError, match="diverged"):
        asyncio.run(calls())


# ------------------------------------------------------------------ keys stay out of MANIFEST


def test_live_mode_needs_a_key_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ModelError, match="gcloud secrets versions access latest --secret=gemini-api-key"):
        make_model("live")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza" + "0" * 35)  # shaped like a key, not a real one
    model, recorder = make_model("live")
    assert model == "gemini-3.8-flash" and recorder is None


def test_replay_and_unknown_modes_fail_clearly(tmp_path: Path) -> None:
    with pytest.raises(ModelError, match="replay needs --recording"):
        make_model("replay")
    with pytest.raises(ModelError, match="recording not found"):
        make_model("replay", recording=tmp_path / "missing.jsonl")
    with pytest.raises(ModelError, match="unknown mode"):
        make_model("guess")


@pytest.mark.parametrize("value", [
    "# 1. Copy the key in AI Studio (click its copy button), then:\npbpaste | gcloud secrets ...",
    "AIzaShort", "AIza" + "x" * 35 + " trailing words",
])  # fmt: skip
def test_a_value_that_isnt_a_key_is_refused_without_showing_it(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", value)
    with pytest.raises(ModelError, match="isn't shaped like a key") as err:
        make_model("live")
    assert value not in str(err.value) and "pbpaste" not in str(err.value)


def test_key_values_are_redacted_from_errors() -> None:
    from manifest.agent import redact

    key = "AIza" + "Q" * 35
    bad = "# copy\npbpaste | gcloud secrets versions add"
    assert redact(f"401 for key={key}", {"GOOGLE_API_KEY": key}) == "401 for key=[redacted]"
    echoed = f"Illegal header value {bad.encode()!r}"
    assert "pbpaste" not in redact(echoed, {"GOOGLE_API_KEY": bad})
