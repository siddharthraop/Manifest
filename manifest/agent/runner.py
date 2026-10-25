"""`manifest fix MAP`: migrate, let the agent work on each failing topic, migrate again, report.

    out/baseline/          the migration with the approved rules only
    out/agent/<topic>/     each fix attempt (convert, myst, check), for the record
    out/agent/proposed/    rules that passed the checks, status "proposed", awaiting a writer
    out/agent/reviews/     one Markdown review issue per topic the agent couldn't fix
    out/agent/run.json     the whole run: every attempt, rule, result and outcome
    out/myst, out/report   the final migration, with approved + proposed rules

Outcomes are decided by the checks. A session ends "fixed" only if an attempt passed; anything
else ends in review, even if the model forgot to call open_review.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import urllib.request
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig
from google.adk.runners import InMemoryRunner
from google.genai import types

from manifest.agent.models import DEFAULT_MODEL, make_model
from manifest.agent.prompt import INSTRUCTION
from manifest.agent.session import FixSession
from manifest.pipeline import migrate
from manifest.rules import Rule, RuleSet, load_rules, save_rule

MAX_MODEL_CALLS = 10  # per topic: read, two fixes, a review, and room for the model to think aloud


def slug(topic: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", Path(topic).stem.lower()).strip("-")


async def _run_session(session: FixSession, model: Any, after: Any) -> None:
    agent = LlmAgent(
        name="manifest_fixer",
        model=model,
        instruction=INSTRUCTION,
        tools=session.tools(),
        generate_content_config=types.GenerateContentConfig(temperature=0),
        after_model_callback=after,
    )
    runner = InMemoryRunner(agent=agent, app_name="manifest")
    s = await runner.session_service.create_session(app_name="manifest", user_id="manifest")
    msg = types.Content(
        role="user", parts=[types.Part(text=f"Fix {session.topic}. Start with read_failure.")]
    )
    async for event in runner.run_async(
        user_id="manifest",
        session_id=s.id,
        new_message=msg,
        run_config=RunConfig(max_llm_calls=MAX_MODEL_CALLS),
    ):
        for part in event.content.parts if event.content and event.content.parts else []:
            if part.text and not part.thought:
                session.final_text = part.text


def review_markdown(session: FixSession, run_id: str) -> str:
    lines = [
        f"# Review: {session.topic}",
        "",
        f"MANIFEST run `{run_id}` couldn't migrate this topic without a difference, and the agent's "
        f"{len(session.attempts)} fix attempt(s) didn't pass the checks. A writer needs to decide.",
        "",
        "## Agent's summary",
        "",
        (session.review or {}).get("summary") or "(The agent didn't write one.)",
        "",
        "## What the checks found",
        "",
    ]
    lines += [
        f"- [{d['build']}] **{d['check']}**: {d['detail']}" for d in session.read_failure()["differences"]
    ]
    for a in session.attempts:
        lines += [
            "",
            f"## Attempt {a.n}: `{a.rule['id']}`",
            "",
            a.rule["description"],
            "",
            "```xml",
            a.rule["xslt"],
            "```",
            "",
            f"Result: {'passed' if a.passed else 'failed'}",
        ]
        if a.error:
            lines.append(f"Error: {a.error}")
        lines += [f"- [{d['build']}] **{d['check']}**: {d['detail']}" for d in a.differences]
    lines += [
        "",
        "## Next steps",
        "",
        "Write or correct a mapping rule (see `rules/README.md`), or accept the difference and record why.",
    ]
    return "\n".join(lines) + "\n"


def open_github_issue(title: str, body: str) -> str | None:
    """Optional: file the review as a GitHub issue when GITHUB_TOKEN and GITHUB_REPOSITORY are set."""
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (token and repo):
        return None
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues",
        data=json.dumps({"title": title, "body": body, "labels": ["manifest-review"]}).encode(),
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 (fixed https URL)
        url: str = json.loads(resp.read())["html_url"]
        return url


def fix_map(
    map_path: Path,
    out: Path,
    mode: str,
    model_id: str = DEFAULT_MODEL,
    recording: Path | None = None,
    rules_dir: Path | None = None,
    script: list[dict[str, Any]] | None = None,
    dita: str | None = None,
    lychee: str | None = None,
    max_attempts: int = 2,
    open_issues: bool = False,
    run_id: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    run_id = run_id or dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    approved = load_rules(rules_dir) if rules_dir else []
    base = out / "baseline"
    say = progress or (lambda _stage: None)
    report = migrate(
        map_path,
        base,
        dita,
        lychee,
        RuleSet(approved) if approved else None,
        progress=lambda st: say(f"baseline: {st}"),
    )
    failing = sorted(t["topic"] for t in report["topics"] if t["status"] == "fail")

    model, after = make_model(mode, model_id, recording, script)
    sessions: list[FixSession] = []
    proposed: list[Rule] = []
    agent_info: dict[str, Any] = {
        "run": run_id,
        "mode": mode,
        "model": model_id if mode == "live" else mode,
        "fixed": {},
        "review": {},
        "approved_rules": [r.id for r in approved],
    }
    for topic in failing:
        session = FixSession(map_path, topic, base, out / "agent" / slug(topic), approved, dita, max_attempts)
        say(f"agent: {topic}")
        asyncio.run(_run_session(session, model, after))
        sessions.append(session)
        win = session.passing_attempt
        if win is not None:
            rule = Rule(
                id=win.rule["id"],
                match=win.rule["match"],
                description=win.rule["description"],
                xslt=win.rule["xslt"],
                status="proposed",
                provenance={
                    "run": run_id,
                    "topic": topic,
                    "proposed_by": str(agent_info["model"]),
                    "attempt": str(win.n),
                    "of_attempts": str(len(session.attempts)),
                },
            )
            save_rule(rule, out / "agent" / "proposed")
            proposed.append(rule)
            agent_info["fixed"][topic] = {"rule": rule.id, "attempts": len(session.attempts)}
        else:
            if session.review is None:
                session.review = {"summary": "", "by": "manifest (the agent didn't open one)"}
            body = review_markdown(session, run_id)
            path = out / "agent" / "reviews" / f"{slug(topic)}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
            issue = open_github_issue(f"MANIFEST review: {topic}", body) if open_issues else None
            agent_info["review"][topic] = {
                "file": str(path.relative_to(out)),
                "issue": issue,
                "attempts": len(session.attempts),
                "by": session.review["by"],
            }

    final_rules = [*approved, *proposed]
    final = migrate(
        map_path,
        out,
        dita,
        lychee,
        RuleSet(final_rules) if final_rules else None,
        agent=agent_info,
        progress=lambda st: say(f"final: {st}"),
    )
    run = {
        **agent_info,
        "map": final["map"],
        "failing_before": failing,
        "gate": final["gate"],
        "sessions": [
            {
                "topic": s.topic,
                "outcome": s.outcome,
                "attempts": [asdict(a) for a in s.attempts],
                "refused_calls": s.refused,
                "review": s.review,
                "final_text": s.final_text,
            }
            for s in sessions
        ],
    }
    (out / "agent").mkdir(parents=True, exist_ok=True)
    (out / "agent" / "run.json").write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    return run
