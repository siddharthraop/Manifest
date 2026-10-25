# Changelog

All notable changes. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [1.1.0] - 2026-10-25

### Added
- Migrate your own DITA: upload a .zip in the console (no sign-in) and download the MyST project, the report and the
  redirects, with a README naming every topic that isn't clean (ADR 0010).
- Live progress on the run page; sample .zip files on the site; white page background.

### Fixed
- Two runs started in the same second could share an ID.

## [1.0.0] - 2026-10-25

### Added
- The five deterministic tools: `inventory`, `convert`, `restore`, `check` and `links`, and `manifest migrate` with a
  JSON and HTML report measuring the design doc's success metrics.
- Mapping rules as validated, sandboxed XSLT templates; `manifest rules approve`.
- `manifest fix`: a Gemini agent (ADK) that proposes rules, at most two per topic, judged by the checks; live, replay and
  scripted modes.
- The cloud runtime: a Cloud Run job and a scale-to-zero API, Firestore and Cloud Storage, deployed without keys.
- The console: overview, maps, run detail (diff, reuse ledger, agent trace), review queue, rules, settings.
- The DITA help set with context-sensitive help, built with DITA-OT and migrated to MyST by MANIFEST itself.
- `manifest lint` and a GitHub Action that annotates pull requests with broken keys, conrefs, links and images.

### Fixed (found by migrating MANIFEST's own help)
- Source text containing `@@` could be read as a placeholder.
- A `|` in a table cell broke the Markdown table.
- A one-step task rendered as a list in MyST but as a paragraph in DITA-OT HTML5.
- Table titles, which DITA-OT's Markdown writer drops, are kept by an approved rule.
