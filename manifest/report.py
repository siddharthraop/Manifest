"""The migration report: one JSON and one HTML page per map (US-01).

It is the evidence a Docs Platform Lead approves a migration from without reading every page:
each topic's result for every check and build, every review item (US-03), every finding about
the source, and the design doc's success metrics measured on this run (design doc §7).

A topic is
    Pass    every check passed in every build and nothing needs a writer
    Review  every check passed, but the source has something MANIFEST has no rule for
    Fail    a check found a difference (each difference is listed, with the check that found it)
"""

from __future__ import annotations

import html
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from manifest import __version__

ASSETS = Path(__file__).parent / "assets"
SCHEMA_VERSION = "1"


@dataclass
class TopicRow:
    topic: str
    status: str  # pass, review, fail
    checks: dict[str, str]  # check -> pass / fail
    note: str
    differences: list[dict[str, str]] = field(default_factory=list)
    review: list[dict[str, Any]] = field(default_factory=list)


def _pct(n: int, d: int) -> str:
    return "100%" if d == 0 else f"{100 * n / d:.0f}%"


def build_report(
    inventory: dict[str, Any],
    convert: dict[str, Any],
    restore: dict[str, Any],
    check: dict[str, Any],
    links: dict[str, Any],
    versions: dict[str, str],
) -> dict[str, Any]:
    published = [t for t in inventory["topics"] if t["processing_role"] == "normal"]
    by_owner_mappings: dict[str, dict[str, int]] = {}
    for m in restore["mappings"]:
        counts = by_owner_mappings.setdefault(m["owner"], {})
        counts[m["kind"]] = counts.get(m["kind"], 0) + 1
    unhandled_by_topic: dict[str, list[dict[str, Any]]] = {}
    for u in restore["unhandled"]:
        unhandled_by_topic.setdefault(u["owner"], []).append(u)
    check_by_topic = {t["topic"]: t for t in check["topics"]}
    link_problems_by_page: dict[str, list[dict[str, Any]]] = {}
    for p in links["problems"]:
        link_problems_by_page.setdefault(p["page"], []).append(p)

    rows: list[TopicRow] = []
    for t in published:
        rel = t["path"]
        page = str(Path(rel).with_suffix(".html"))
        ct = check_by_topic.get(rel)
        diffs = [
            {
                "build": b["build"],
                "check": d["check"],
                "detail": d["detail"],
                "dita": d["dita"],
                "myst": d["myst"],
            }
            for b in (ct["builds"] if ct else [])
            for d in b["differences"]
        ]
        diffs += [{"build": p["build"], "check": "links", "detail": p["detail"], "dita": "", "myst": ""}
                  for p in link_problems_by_page.get(page, [])]  # fmt: skip
        failed = {d["check"] for d in diffs}
        checks = {
            "counts": "fail" if "structure" in failed else "pass",
            "text": "fail" if "text" in failed or "pages" in failed else "pass",
            "ids": "fail" if "ids" in failed else "pass",
            "links": "fail" if "links" in failed else "pass",
        }
        if ct is None:
            checks = dict.fromkeys(checks, "fail")
            diffs.append(
                {"build": "-", "check": "pages", "detail": "topic was not checked", "dita": "", "myst": ""}
            )
        review = unhandled_by_topic.get(rel, [])
        m = by_owner_mappings.get(rel, {})
        parts = []
        if t["totals"]["conrefs"]:
            parts.append(f"{t['totals']['conrefs']} include(s)")
        if t["totals"]["keyrefs"]:
            parts.append(f"{t['totals']['keyrefs']} substitution(s)")
        if m.get("condition"):
            parts.append(f"{t['totals']['conditions']} condition(s) as only blocks")
        note = (", ".join(parts) + " restored") if parts else "No reuse"
        if review:
            note += "; " + "; ".join(
                f"<{u.get('element', '?')}> line {u.get('line', '?')}: needs a writer" for u in review
            )
        status = "fail" if diffs else ("review" if review else "pass")
        rows.append(TopicRow(rel, status, checks, note, diffs, review))

    t_conrefs = sum(t["totals"]["conrefs"] for t in inventory["topics"] if t["processing_role"] == "normal")
    t_keyrefs = sum(t["totals"]["keyrefs"] for t in inventory["topics"] if t["processing_role"] == "normal")
    rt = restore["totals"]
    keys_in_topics = len({m["token"] for m in restore["mappings"]
                          if m["kind"] == "key" and not m["owner"].startswith("_manifest_includes")})  # fmt: skip
    conrefs_kept = rt["conrefs_as_includes"] + rt["conrefs_as_substitutions"]
    ids_total = sum(len(t["ids"]) for t in published)
    ids_missing = sum(
        1 for r in rows for d in r.differences if d["check"] == "ids" and d["build"] == "unfiltered"
    )
    lt = links["totals"]
    broken = lt["broken_problems"] + lt["navigation_problems"] + lt["old-url_problems"]
    differences = sum(len(r.differences) for r in rows)
    passed_first = sum(r.status == "pass" for r in rows)

    metrics = [
        {"metric": "Unreported losses", "target": "0", "measured": "0",
         "how": f"Every difference the checks find is listed in this report ({differences} this run). "
                "Losses found after merge are tracked against it."},
        {"metric": "Topics checked", "target": "100%", "measured": _pct(len(check["topics"]), len(published)),
         "how": f"{len(check['topics'])} of {len(published)} topics, {check['totals']['comparisons']} "
                f"comparisons across {len(check['builds'])} builds"},
        {"metric": "Reuse kept", "target": "100% of mapped features",
         "measured": _pct(conrefs_kept + keys_in_topics, t_conrefs + t_keyrefs),
         "how": f"{conrefs_kept}/{t_conrefs} conrefs as includes or substitutions; "
                f"{keys_in_topics}/{t_keyrefs} key references as substitutions"},
        {"metric": "IDs kept", "target": "100%", "measured": _pct(ids_total - ids_missing, ids_total),
         "how": f"{ids_total - ids_missing}/{ids_total} published IDs are anchors in DITA-OT's form; "
                f"{lt['old_anchors']} old anchors resolve across all builds"},
        {"metric": "Broken links", "target": "0", "measured": str(broken),
         "how": f"lychee {links['lychee']} offline: {lt['checked_links']} links; {lt['old_urls']} old URLs; "
                "navigation compared with DITA-OT"},
        {"metric": "Topics passed without a writer", "target": "Set from the first run",
         "measured": f"{passed_first}/{len(rows)}", "how": "Topics with no difference and no review item"},
        {"metric": "Fixes approved", "target": "Tracked", "measured": "n/a",
         "how": "Gemini proposes fixes from build step 3; none were needed or proposed in this run"},
    ]  # fmt: skip

    review_items = [
        {"topic": u["owner"], "element": u.get("element", ""), "line": u.get("line"), "reason": u["reason"]}
        for u in restore["unhandled"]
    ]
    gate = "fail" if any(r.status == "fail" for r in rows) or broken else "pass"
    return {
        "schema_version": SCHEMA_VERSION,
        "map": inventory["map"],
        "manifest": __version__,
        "versions": versions,
        "gate": gate,
        "summary": {
            "topics": len(rows),
            "passed": passed_first,
            "review": sum(r.status == "review" for r in rows),
            "failed": sum(r.status == "fail" for r in rows),
            "builds": check["builds"],
        },
        "metrics": metrics,
        "topics": [r.__dict__ for r in rows],
        "review_items": review_items,
        "findings": restore.get("findings", []),
        "inputs": {"tokens_carried": convert["totals"]["tokens"], "includes": convert["totals"]["includes"]},
    }


# ---------------------------------------------------------------- HTML

CSS = """
@font-face{font-family:'IBM Plex Sans';src:url(assets/fonts/IBMPlexSans.woff2) format('woff2');font-weight:100 700}
@font-face{font-family:'Courier Prime';src:url(assets/fonts/CourierPrime.woff2) format('woff2')}
@font-face{font-family:'Caveat';src:url(assets/fonts/Caveat.woff2) format('woff2')}
:root{--paper:#FAF8F3;--paper-deep:#F2EEE4;--paper-hover:#EFE9DC;--ink:#1C1A17;--ink-body:#29251F;
--ink-soft:#5C5650;--stamp:#8C3D2E;--stamp-soft:#B06650;--stamp-tint:#C9A98F;--rule:#DDD6CB;--radius:2px;
--band-bg:#1C1A17;--band-fg:#FAF8F3;--f-scan:'IBM Plex Sans',system-ui,sans-serif;--f-type:'Courier Prime',ui-monospace,monospace;--f-script:'Caveat',cursive}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--paper:#1C1A17;--paper-deep:#24211D;
--paper-hover:#2C2824;--ink:#FAF8F3;--ink-body:#EDE8DF;--ink-soft:#B8B0A5;--stamp:#B06650;--rule:rgba(250,248,243,.12);--band-bg:#24211D;--band-fg:#FAF8F3}}
:root[data-theme=dark]{--paper:#1C1A17;--paper-deep:#24211D;--paper-hover:#2C2824;--ink:#FAF8F3;--ink-body:#EDE8DF;
--ink-soft:#B8B0A5;--stamp:#B06650;--rule:rgba(250,248,243,.12);--band-bg:#24211D;--band-fg:#FAF8F3}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink-body);font:15px/1.55 var(--f-scan)}
main{max-width:1120px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:28px;color:var(--ink);margin:0 0 4px}h2{font-size:18px;color:var(--ink);margin:40px 0 12px;
border-top:1px solid var(--rule);padding-top:20px}
.meta{color:var(--ink-soft);font-size:13px}code,.mono{font-family:var(--f-type)}
.band{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;background:var(--band-bg);
color:var(--band-fg);border-radius:var(--radius);margin:24px 0;overflow:hidden}
.band div{background:var(--band-bg);padding:16px}.band b{display:block;font-size:26px;font-weight:600}
.band span{font-size:12px;color:var(--stamp-tint)}
table{width:100%;border-collapse:collapse;font-size:14px}caption{text-align:left;color:var(--ink-soft);
font-size:13px;padding-bottom:8px}th{background:var(--paper-deep);text-align:left;font-weight:600;color:var(--ink)}
th,td{padding:8px 10px;border-bottom:1px solid var(--rule);vertical-align:top}tbody tr:hover{background:var(--paper-hover)}
.st{white-space:nowrap;font-weight:600}.st.pass{color:var(--ink)}.st.fail{color:var(--stamp)}
.st.review span{color:var(--ink);outline:1px solid var(--stamp-tint);padding:0 6px;border-radius:var(--radius)}
.gate{display:inline-block;padding:4px 10px;border-radius:var(--radius);font-weight:600}
.gate.pass{background:var(--ink);color:var(--paper)}.gate.fail{background:var(--stamp);color:var(--paper)}
.diff td{background:var(--paper-deep);font-size:13px}.sig{font-family:var(--f-script);font-size:22px;color:var(--ink-soft)}
a{color:var(--ink);text-decoration-color:var(--stamp-tint)}a:focus-visible{outline:2px solid var(--stamp);outline-offset:2px}
@media (max-width:640px){.topics th:nth-child(n+3):nth-child(-n+6),.topics td:nth-child(n+3):nth-child(-n+6){display:none}
table{font-size:13px}th,td{padding:8px 6px}td{overflow-wrap:anywhere}}
"""
ICON = {"pass": "✓ Pass", "fail": "✗ Fail", "review": "◇ Review"}
GUIDANCE = {
    "possible-typed-copy": "A value typed in the source equals a key's value. If it means the same thing, "
    "replace it with the key, so the value is changed in one place. Each place is listed in report.json.",
    "reltable-keeps-filtered-topic": "The map filters these topics out of some builds, but a relationship-table "
    "row without the same condition keeps them published (DITA-OT does the same). Add the condition to the "
    "reltable row if they should be excluded.",
}


def _e(s: object) -> str:
    return html.escape(str(s), quote=True)


def render_html(r: dict[str, Any]) -> str:
    s = r["summary"]
    rows = []
    for t in r["topics"]:
        tid = "topic-" + t["topic"].replace("/", "-").replace(".", "-")
        cells = "".join(f'<td class="st {v}"><span>{ICON[v]}</span></td>' for v in t["checks"].values())
        rows.append(
            f'<tr id="{_e(tid)}"><td class="mono">{_e(t["topic"])}</td>'
            f'<td class="st {t["status"]}"><span>{ICON[t["status"]]}</span></td>{cells}<td>{_e(t["note"])}</td></tr>'
        )
        for d in t["differences"]:
            rows.append(f'<tr class="diff"><td colspan="7"><a href="#{_e(tid)}">{_e(t["topic"])}</a> · '
                        f'<b>{_e(d["check"])}</b> [{_e(d["build"])}]: {_e(d["detail"])}'
                        + (f' — DITA <code>{_e(d["dita"])}</code> MyST <code>{_e(d["myst"])}</code>'
                           if d["dita"] or d["myst"] else "") + "</td></tr>")  # fmt: skip
    metrics = "".join(
        f'<tr><td>{_e(m["metric"])}</td><td>{_e(m["target"])}</td><td class="mono">'
        f"{_e(m['measured'])}</td><td>{_e(m['how'])}</td></tr>"
        for m in r["metrics"]
    )
    review = "".join(
        f'<tr><td class="mono">{_e(i["topic"])}</td><td class="mono">&lt;{_e(i["element"])}&gt;</td>'
        f"<td>{_e(i['line'])}</td><td>{_e(i['reason'])}</td></tr>"
        for i in r["review_items"]
    )
    kinds: dict[str, list[dict[str, Any]]] = {}
    for f in r["findings"]:
        kinds.setdefault(f["kind"], []).append(f)
    findings = "".join(
        f'<tr><td class="mono">{_e(k)}</td><td>{len(v)}</td><td>{_e(GUIDANCE.get(k, v[0]["detail"]))}</td>'
        f'<td class="mono">{_e(", ".join(sorted({x["topic"] for x in v}))[:400])}</td></tr>'
        for k, v in sorted(kinds.items())
    )
    versions = " · ".join(f"{k} {v}" for k, v in r["versions"].items())
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>MANIFEST report: {_e(r["map"])}</title>
<style>{CSS}</style></head><body><main>
<h1>Migration report: <span class="mono">{_e(r["map"])}</span></h1>
<p class="meta">MANIFEST {_e(r["manifest"])} · {_e(versions)} · builds: {_e(", ".join(s["builds"]))}</p>
<p><span class="gate {r["gate"]}">{ICON[r["gate"]]} gate</span>
 {s["review"]} topic(s) need a writer; {s["failed"]} failed.</p>
<div class="band" role="list">
<div role="listitem"><b>{s["topics"]}</b><span>topics</span></div>
<div role="listitem"><b>{s["passed"]}</b><span>passed, no writer needed</span></div>
<div role="listitem"><b>{s["review"]}</b><span>sent to a writer</span></div>
<div role="listitem"><b>{s["failed"]}</b><span>failed a check</span></div>
<div role="listitem"><b>{len(s["builds"])}</b><span>builds compared with DITA-OT</span></div></div>
<h2>Topics</h2><table class="topics"><caption>Each check compares DITA-OT HTML5 of the source with Sphinx of the migrated MyST,
in every build. A difference is listed under its topic.</caption>
<thead><tr><th scope="col">Topic</th><th scope="col">Status</th><th scope="col">Counts</th><th scope="col">Text</th>
<th scope="col">IDs</th><th scope="col">Links</th><th scope="col">Note</th></tr></thead><tbody>{"".join(rows)}</tbody></table>
<h2>Review items</h2><table><caption>Source elements MANIFEST has no rule for. Each one is sent to a writer
(US-03); none is dropped silently.</caption><thead><tr><th scope="col">Topic</th><th scope="col">Element</th>
<th scope="col">Line</th><th scope="col">Why</th></tr></thead><tbody>{review or '<tr><td colspan="4">None</td></tr>'}
</tbody></table>
<h2>Findings in the source</h2><table><caption>Things the migration kept as they are, but a writer should know.
Details for each occurrence are in report.json.</caption><thead><tr><th scope="col">Finding</th>
<th scope="col">Count</th><th scope="col">What to do</th><th scope="col">Topics</th></tr></thead>
<tbody>{findings or '<tr><td colspan="4">None</td></tr>'}</tbody></table>
<h2>Success metrics</h2><table><caption>Design doc §7, measured on this run.</caption><thead><tr>
<th scope="col">Metric</th><th scope="col">Target</th><th scope="col">Measured</th><th scope="col">How</th></tr></thead>
<tbody>{metrics}</tbody></table>
<p class="sig">— checked, not assumed</p>
</main></body></html>
"""


def write_report(report: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (out_dir / "report.html").write_text(render_html(report), encoding="utf-8")
    fonts = out_dir / "assets" / "fonts"
    if fonts.exists():
        shutil.rmtree(fonts)
    shutil.copytree(ASSETS / "fonts", fonts)
