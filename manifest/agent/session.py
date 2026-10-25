"""One agent session: one failing topic, three tools, at most two fixes.

The tools are the only way the model touches anything. Every rule it proposes is validated, run
against the whole map, and judged by the deterministic checks; the session, not the model,
decides whether a fix passed. A third try_fix is refused.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree

from manifest.rules import Rule, RuleError, RuleSet, validate
from manifest.tools.check import _content, check_topics
from manifest.tools.convert import convert_map
from manifest.tools.restore import restore_reuse

MAX_CHARS = 9000  # per document handed to the model


def _clip(text: str, n: int = MAX_CHARS) -> str:
    return text if len(text) <= n else text[:n] + f"\n… ({len(text) - n} more characters)"


def _load(p: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    return data


def _differences(check: dict[str, Any], topic: str, limit: int = 20) -> list[dict[str, str]]:
    t = next((x for x in check["topics"] if x["topic"] == topic), None)
    out = [
        {
            "build": b["build"],
            "check": d["check"],
            "detail": d["detail"],
            "dita": d["dita"],
            "myst": d["myst"],
        }
        for b in (t["builds"] if t else [])
        for d in b["differences"]
    ]
    return out[:limit]


@dataclass
class Attempt:
    n: int
    rule: dict[str, Any]
    valid: bool
    passed: bool
    error: str = ""
    differences: list[dict[str, str]] = field(default_factory=list)
    unhandled: list[str] = field(default_factory=list)
    other_topics_broken: list[str] = field(default_factory=list)


class FixSession:
    def __init__(
        self,
        map_path: Path,
        topic: str,
        baseline: Path,
        work: Path,
        approved: list[Rule],
        dita: str | None = None,
        max_attempts: int = 2,
    ) -> None:
        self.map_path, self.topic, self.baseline, self.work = map_path, topic, baseline, work
        self.approved, self.dita, self.max_attempts = approved, dita, max_attempts
        self.attempts: list[Attempt] = []
        self.refused = 0
        self.review: dict[str, Any] | None = None
        self.final_text = ""
        check = _load(baseline / "check" / "check.json")
        t = next(x for x in check["topics"] if x["topic"] == topic)
        self.builds = [b["build"] for b in t["builds"]]
        self.baseline_failing = {x["topic"] for x in check["topics"] if not x["ok"]}
        self.baseline_check = check

    # ------------------------------------------------------------- what the model can call

    def tools(self) -> list[Any]:
        session = self

        def read_failure() -> dict[str, Any]:
            """Read why the topic failed: the check's differences, the DITA source, DITA-OT's HTML5
            rendering of the source (what the migrated page must match) and the migrated MyST."""
            return session.read_failure()

        def try_fix(rule_id: str, matches: str, description: str, xslt: str) -> dict[str, Any]:
            """Try one complete mapping rule and run the checks again.

            Args:
                rule_id: 3-64 lowercase letters, digits and hyphens, e.g. "choicetable-to-simpletable".
                matches: the DITA element names the rule rewrites, separated by spaces.
                description: one sentence a technical writer can review.
                xslt: one or more <xsl:template match="..."> elements (XSLT 1.0), nothing else.
            """
            return session.try_fix(rule_id, matches, description, xslt)

        def open_review(summary: str) -> dict[str, Any]:
            """Send the topic to a technical writer. Use it when two fixes have failed.

            Args:
                summary: what the element is, what you tried, and what still differs.
            """
            return session.open_review(summary)

        return [read_failure, try_fix, open_review]

    # ------------------------------------------------------------- tool bodies

    def read_failure(self) -> dict[str, Any]:
        src = (self.map_path.parent / self.topic).read_text(encoding="utf-8")
        src = re.sub(r"<prolog>.*?</prolog>", "<prolog>…</prolog>", src, flags=re.S)
        page = str(Path(self.topic).with_suffix(".html"))
        dita_html = _content(self.baseline / "check" / "dita-html" / self.builds[0] / page, "dita")
        for el in dita_html.iter():
            for a in ("style", "cellpadding", "cellspacing", "frame", "rules", "summary", "border"):
                el.attrib.pop(a, None)
        restore = _load(self.baseline / "myst" / "_manifest" / "restore.json")
        return {
            "topic": self.topic,
            "builds_checked": self.builds,
            "attempts_left": self.max_attempts - len(self.attempts),
            "differences": _differences(self.baseline_check, self.topic),
            "unhandled": [u["reason"] for u in restore["unhandled"] if u["owner"] == self.topic],
            "dita_source": _clip(src),
            "dita_ot_html5": _clip(etree.tostring(dita_html, encoding="unicode")),
            "migrated_myst": _clip(
                (self.baseline / "myst" / Path(self.topic).with_suffix(".md")).read_text()
            ),
            "approved_rules": [r.id for r in self.approved],
        }

    def try_fix(self, rule_id: str, matches: str, description: str, xslt: str) -> dict[str, Any]:
        if len(self.attempts) >= self.max_attempts:
            self.refused += 1
            return {
                "refused": True,
                "message": f"{self.max_attempts} fixes have been tried. Call open_review.",
            }
        n = len(self.attempts) + 1
        rule = Rule(id=rule_id, match=matches, description=description, xslt=xslt, status="proposed")
        record = {"id": rule_id, "match": matches, "description": description, "xslt": xslt}
        try:
            validate(rule)
            rules = RuleSet([*self.approved, rule])
        except RuleError as exc:
            self.attempts.append(Attempt(n, record, valid=False, passed=False, error=str(exc)))
            return {"passed": False, "error": str(exc), "attempts_left": self.max_attempts - n}

        out = self.work / f"attempt-{n}"
        if out.exists():
            shutil.rmtree(out)
        try:
            conv = convert_map(self.map_path, out / "convert", self.dita, rules)
            if not conv.ok:
                raise RuleError("the rule made MANIFEST lose placeholders; the topic's reuse would break")
            res = restore_reuse(self.map_path, out / "convert", out / "myst")
            chk = check_topics(self.map_path, out / "myst", out / "check", self.dita,
                               only_builds=self.builds, dita_html_from=self.baseline / "check")  # fmt: skip
        except Exception as exc:  # a rule can break the pipeline; that's a failed fix, not a crash
            self.attempts.append(Attempt(n, record, valid=True, passed=False, error=str(exc)[:500]))
            return {"passed": False, "error": str(exc)[:500], "attempts_left": self.max_attempts - n}

        check = _load(out / "check" / "check.json")
        topic_ok = next(t for t in chk.topics if t.topic == self.topic).ok
        unhandled = [u["reason"] for u in res.unhandled if u["owner"] == self.topic]
        broken = sorted({t.topic for t in chk.topics if not t.ok} - self.baseline_failing)
        passed = topic_ok and not unhandled and not broken and not any(chk.page_differences.values())
        att = Attempt(n, record, valid=True, passed=passed, differences=_differences(check, self.topic, 12),
                      unhandled=unhandled, other_topics_broken=broken)  # fmt: skip
        self.attempts.append(att)
        return {
            "passed": passed,
            "differences": att.differences,
            "unhandled": unhandled,
            "other_topics_broken": broken,
            "attempts_left": self.max_attempts - n,
        }

    def open_review(self, summary: str) -> dict[str, Any]:
        self.review = {"summary": summary.strip(), "by": "agent"}
        return {"opened": True}

    # ------------------------------------------------------------- outcome (decided here, not by the model)

    @property
    def passing_attempt(self) -> Attempt | None:
        return next((a for a in reversed(self.attempts) if a.passed), None)

    @property
    def outcome(self) -> str:
        return "fixed" if self.passing_attempt else "review"
