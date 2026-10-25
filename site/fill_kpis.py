"""Fill the landing page's results band from the TPU report, so its numbers are always this build's."""

import html
import json
import re
import sys
from pathlib import Path

page, report = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
m = {x["metric"]: x for x in report["metrics"]}
s = report["summary"]
cells = [
    (m["Unreported losses"]["measured"], "unreported losses"),
    (m["Reuse kept"]["measured"], "reuse kept as includes and substitutions"),
    (m["IDs kept"]["measured"], "IDs kept as anchors"),
    (m["Broken links"]["measured"], "broken links, old URLs included"),
    (f"{s['passed']}/{s['topics']}", "topics passed without a writer"),
    ("$0", "a month on Google Cloud's free tier"),
]
band = "".join(
    f'<div role="listitem"><b>{html.escape(str(v))}</b><span>{html.escape(t)}</span></div>' for v, t in cells
)
note = ""
if report["gate"] != "pass":
    failing = [t["topic"] for t in report["topics"] if t["status"] == "fail"]
    note = (
        f"<p>This build's gate is <b>fail</b>: {html.escape(', '.join(failing))} needs a mapping rule. "
        "That is the agent's job: it proposes the rule, the checks test it, "
        "and a writer approves it in the console.</p>"
    )
new = (
    f'<div class="band" role="list" aria-label="Results on the TPU sample map, this build">{band}</div>{note}'
    '<p><a href="migrated/tpu-report/report.html">How each number was measured</a></p>'
)
text = page.read_text(encoding="utf-8")
page.write_text(re.sub(r"<!--KPIS-->.*?<!--/KPIS-->", lambda _: new, text, flags=re.S), encoding="utf-8")
