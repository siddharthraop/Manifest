# Architecture

MANIFEST has one job: migrate a DITA map to MyST Markdown for Sphinx and prove that every topic arrived complete.
Decisions are recorded in [`docs/adr/`](../adr/); this page is the map.

```
            ┌────────────── manifest (Python package) ──────────────┐
DITA map ──▶│ inventory → convert → restore → check → links → report │──▶ MyST project + report.json/html
            │        ▲                                   │          │
            │        └──── rules (approved XSLT) ◀── agent (Gemini) │  manifest fix: ≤ 2 fixes per topic
            └────────────────────────────────────────────────────────┘
   CLI (manifest …)   ·   Cloud Run job (manifest.cloud)   ·   GitHub Action (manifest lint)
                               │ Firestore: runs, rules, reviews, audit
                               │ Cloud Storage: runs/{id}/ report, MyST, ledger, agent record
   Console (static, Pages) ──▶ API (FastAPI on Cloud Run) ──▶ starts the job · reads both stores
```

| Part | Code | Notes |
| --- | --- | --- |
| Inventory | `manifest/tools/inventory.py` | lxml, per topic: elements, IDs, keyrefs, conrefs, conditions, links, media, with line numbers |
| Convert | `manifest/tools/convert.py`, `manifest/placeholders.py` | Numbered placeholders + ledger, then DITA-OT 4.4 `markdown` (ADR 0002) |
| Restore | `manifest/tools/restore.py` | Placeholders → includes, substitutions, `only` blocks, anchors; one Sphinx build per DITAVAL (ADR 0003) |
| Check | `manifest/tools/check.py` | DITA-OT HTML5 vs Sphinx, per topic, per build: text, structure, IDs (ADR 0004) |
| Links | `manifest/tools/links.py` | lychee offline, navigation, old URLs and redirect stubs (ADR 0005) |
| Report | `manifest/report.py` | JSON + HTML with the design doc §7 metrics |
| Rules | `manifest/rules.py` | Validated, sandboxed XSLT 1.0 templates (ADR 0006) |
| Agent | `manifest/agent/` | ADK `LlmAgent`, three tools, limits enforced in code; live, replay and scripted modes (ADR 0007) |
| Cloud | `manifest/cloud.py`, `manifest/store.py`, `api/` | Job, store and API (ADR 0008) |
| Lint | `manifest/tools/lint.py`, `action/` | The pull-request check (US-04) |
| Console | `console/` | Static, no framework; CSH from `help/` (ADR 0009) |
| Help | `help/` | DITA help set, `csh.json`, OpenAPI → DITA reference (ADR 0009) |

The invariant: **the checks decide.** Nothing the model writes can change a check result, and every rule a model
proposes is run against the whole map and approved by a person before it runs again.
