# MANIFEST

MANIFEST migrates DITA XML documentation to docs as code. It converts a DITA map to MyST Markdown, checks every topic for
lost content, and sends anything it can't fix to a technical writer.

**[Read the design document](https://siddharthraop.github.io/Manifest/)**

## Status

**The engine is built and passes on the sample map.** `manifest migrate` runs the
five deterministic tools and writes a migration report. The Gemini agent, the
Cloud Run runtime, the console and the DITA help set are the next build steps.

On `corpus/tpu/tpu.ditamap` (23 published topics, 5 builds: unfiltered and one
per DITAVAL file):

| Design doc §7 metric | Target | Measured |
| --- | --- | --- |
| Unreported losses | 0 | 0 |
| Topics checked | 100% | 100% (112 comparisons with DITA-OT HTML5) |
| Reuse kept | 100% of mapped features | 100% (11 conrefs, 169 key references) |
| IDs kept | 100% | 100% (51 IDs, in DITA-OT's anchor form) |
| Broken links | 0 | 0 (3,110 links; 112 old URLs) |
| Topics passed without a writer | Set from the first run | 22 of 23 |

The one topic for a writer has an `<object>` video that DITA-OT's Markdown
writer drops without a warning. On the way, the checks found four losses that a
plain DITA-OT conversion ships, and two issues in the source map. See
[the ADRs](docs/adr/).

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

## View the design document locally

The design document is a single HTML file with no build step.

1. Clone the repository:

   ```sh
   git clone https://github.com/siddharthraop/Manifest.git
   ```

1. Open `index.html` in a web browser.

## Repository contents

| Path | Description |
| --- | --- |
| `manifest/` | The engine: the five tools, the report and the `manifest` CLI |
| `tests/` | One test file per user story (`test_us01_report.py` …), per metric and per tool |
| `corpus/` | The [dita-corpus](https://github.com/siddharthraop/dita-corpus) sample, pinned as a submodule |
| `infra/` | One-time Google Cloud setup (`setup.sh`) |
| `docs/adr/` | Architecture decision records |
| `index.html` | The design document, published with GitHub Pages |
| `.github/workflows/ci.yml` | Lint, types, tests and the end-to-end migration gate |

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
