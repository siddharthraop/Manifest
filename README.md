# MANIFEST

MANIFEST migrates DITA XML documentation to docs as code. It converts a DITA map to MyST Markdown, checks every topic for
lost content, and sends anything it can't fix to a technical writer.

**[Open MANIFEST](https://siddharthraop.github.io/Manifest/)** · [design document](https://siddharthraop.github.io/Manifest/design/)

## Status

**Version 1.0.0.** Engine, fix agent, cloud runtime, console, online help and the pull-request check are built and deployed.

| | |
| --- | --- |
| Site | https://siddharthraop.github.io/Manifest/ |
| Console | https://siddharthraop.github.io/Manifest/app/ |
| Help center (DITA) | https://siddharthraop.github.io/Manifest/help/ |
| MANIFEST's own help, migrated by MANIFEST | https://siddharthraop.github.io/Manifest/migrated/help/ |
| Design document | https://siddharthraop.github.io/Manifest/design/ |
| Developer docs | [docs/dev/](docs/dev/) · [ADRs](docs/adr/) · [CHANGELOG](CHANGELOG.md) |

On `corpus/tpu/tpu.ditamap` (24 published topics, 5 builds): 0 unreported losses, 100% of reuse kept, 22 of 24 topics
pass without a writer. Of the other two, one has an `<object>` video that DITA-OT drops, so it goes to a writer; the other
(`tpu-v5e-slice-planning`, with a choice table and a hazard statement) is the agent's job. Gemini proposes a rule, the
checks test it, and once a writer approves it every §7 metric is met: the test suite proves this path with a scripted
model ([ADR 0007](docs/adr/0007-the-fix-agent.md)). MANIFEST's own help migrates with the gate passing
([ADR 0009](docs/adr/0009-console-help-and-site.md)).

## Quick start

You need Python 3.11+, Java 17+, [DITA-OT 4.4](https://www.dita-ot.org/download)
and [lychee](https://github.com/lycheeverse/lychee).

```sh
git clone --recurse-submodules https://github.com/siddharthraop/Manifest.git
cd Manifest
pip install -e '.[sphinx]'
export DITA_HOME=/path/to/dita-ot-4.4 LYCHEE=/path/to/lychee
manifest migrate corpus/tpu/tpu.ditamap
```

Open `build/report/report.html` for the report, and `build/myst/` for the
migrated Sphinx project. Each tool also runs on its own: `manifest inventory`,
`convert`, `restore`, `check` and `links`.

### The fix agent

```sh
pip install -e '.[sphinx,agent]'
# live: Gemini, with the key read from Secret Manager for this one command, every response recorded
GOOGLE_API_KEY="$(gcloud secrets versions access latest --secret=gemini-api-key)" \
  manifest fix corpus/tpu/tpu.ditamap --mode live --recording recordings/tpu.jsonl
# replay: the same run from the recording, with no key and no cost
manifest fix corpus/tpu/tpu.ditamap --mode replay --recording recordings/tpu.jsonl
# a writer approves a proposed rule; from then on migrate applies it without Gemini
manifest rules approve build/agent/proposed/RULE.json --by "Your Name"
```

Proposed rules are in `build/agent/proposed/`, review issues in
`build/agent/reviews/`, and every attempt in `build/agent/run.json`.

### The cloud runtime

One image runs both the migration job and the API. `deploy.yml` deploys it
from `main` through Workload Identity Federation, with no keys
([infra/README.md](infra/README.md)). To run the API on a laptop against
memory:

```sh
pip install -e '.[sphinx,agent,cloud]'
uvicorn --factory api.main:create_local_app   # then open http://127.0.0.1:8000/docs
```

## The problem

DITA lets writers reuse content. A writer writes a note or a spec value once
and references it from many topics. DITA Open Toolkit (DITA-OT) can already
convert DITA to Markdown with one command, but before it writes any output it
resolves that reuse:

- Content references (conrefs) are copied into every topic.
- Key references are written as plain text.
- Conditional content is filtered for one audience.
- In the GitHub-Flavored Markdown format, element IDs are dropped.

The conversion doesn't report any of these changes. On a documentation set
with thousands of topics, no team can check every page by hand, so the losses
reach readers.

## What MANIFEST does

MANIFEST does one thing: it migrates a DITA map and proves that each topic is
complete.

1. It counts what is in the DITA source.
1. It converts the map with DITA-OT, using placeholders so that reuse survives
   the conversion.
1. It restores the reuse as MyST Markdown includes, substitutions, and
   conditional blocks.
1. It checks each topic against the source: element counts, text, IDs, and
   links.
1. Topics that pass go into a pull request. When a topic fails, Gemini
   proposes a fix and the check runs again. After two failed fixes, the agent
   opens a review issue for a writer.

The checks decide whether a topic passes. The agent can't change a check
result.

### How DITA features map to MyST Markdown

| DITA feature | MyST Markdown | How the check confirms it |
| --- | --- | --- |
| Content reference (conref) | Include file, written once | Includes in the output equal conrefs in the source |
| Key reference | Substitution | Substitutions equal key references |
| Conditional content | `only` block with build tags | Text matches DITA-OT output for the same DITAVAL file |
| Element ID | Anchor in DITA-OT's form (`topicid__elemid`) | Every published ID is an anchor; old URLs resolve |
| Elements with no rule (`<object>`, conref push) | None | Sent to a writer for review |

## Technology

| Part | Choice |
| --- | --- |
| Agent framework | [Agent Development Kit (ADK)](https://google.github.io/adk-docs/) |
| Model | Gemini 3.8 Flash (`gemini-3.8-flash`) |
| Conversion | [DITA Open Toolkit](https://www.dita-ot.org/) 4.4 |
| Inventory and checks | Python with lxml |
| Target docs site | [Sphinx](https://www.sphinx-doc.org/) with [MyST Markdown](https://myst-parser.readthedocs.io/) |
| Link checks | [lychee](https://github.com/lycheeverse/lychee) |
| Runtime | Cloud Run and GitHub Actions |

## Repository contents

| Path | Description |
| --- | --- |
| `manifest/` | The engine: five tools, report, rules, agent, cloud job, store, `manifest` CLI |
| `api/` | The FastAPI service (Cloud Run) |
| `console/` | The console: a static app with context-sensitive help |
| `help/` | The DITA help set, its CSH builder and the OpenAPI-to-DITA generator |
| `action/` | The GitHub Action for pull requests (US-04) |
| `rules/` | Approved mapping rules |
| `recordings/` | Recorded Gemini sessions for replay |
| `site/` | The landing page and the site build script |
| `design/` | The design document |
| `tests/` | One test file per user story, per metric and per tool, plus the help gate |
| `corpus/` | The [dita-corpus](https://github.com/siddharthraop/dita-corpus) sample, pinned as a submodule |
| `infra/` | Google Cloud setup and the Cloud Run definitions |
| `docs/` | Developer docs (`docs/dev/`) and architecture decision records (`docs/adr/`) |
| `.github/workflows/` | `ci.yml`, `deploy.yml` (WIF → Cloud Run), `pages.yml` (site) |

## Sample content and attribution

The sample content in the design document is adapted from the
[Google Cloud TPU documentation](https://docs.cloud.google.com/tpu/docs/tpu7x),
which Google publishes under the
[Creative Commons Attribution 4.0 License](https://creativecommons.org/licenses/by/4.0/).
The adapted content is modified from the original.

This is a portfolio project. Google has not engaged, sponsored, or endorsed
it. Stakeholders in the design document are job roles, not real people.

## Contact

Siddharth Rao ([@siddharthraop](https://github.com/siddharthraop)). To report
a problem or ask a question, open an
[issue](https://github.com/siddharthraop/Manifest/issues).

## License

This project is licensed under the MIT License. For details, see
[LICENSE](LICENSE). Content adapted from Google documentation remains under
the Creative Commons Attribution 4.0 License.
