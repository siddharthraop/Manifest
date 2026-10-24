# MANIFEST

MANIFEST is a design for an agent that migrates DITA XML documentation to
docs as code. It converts a DITA map to MyST Markdown, checks every topic for
lost content, and sends anything it can't fix to a technical writer.

**[Read the design document](https://siddharthraop.github.io/Manifest/)**

## Status

This project is in the design phase. The design document is complete. The
agent is not built yet, and this repository contains no runnable code.

The simulator in the design document is scripted. It shows how the agent is
designed to behave. It doesn't call Gemini or DITA-OT.

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
| Element ID | Target | Every source ID is an anchor |
| Specialized elements | No rule | Sent to a writer for review |

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

| File | Description |
| --- | --- |
| `index.html` | The design document, published with GitHub Pages |
| `README.md` | This file |
| `LICENSE` | MIT License |
| `.gitignore` | Files that Git doesn't track |

## Sample content and attribution

The sample content in the design document is adapted from the
[Google Cloud TPU documentation](https://docs.cloud.google.com/tpu/docs/tpu7x),
which Google publishes under the
[Creative Commons Attribution 4.0 License](https://creativecommons.org/licenses/by/4.0/).
The adapted content is modified from the original.

This is a portfolio project. Google has not engaged, sponsored, or endorsed
it. Stakeholders in the design document are job roles, not real people.

## Contact

Siddharth Rao ([@raosiddharthp](https://github.com/raosiddharthp)). To report
a problem or ask a question, open an
[issue](https://github.com/siddharthraop/Manifest/issues).

## License

This project is licensed under the MIT License. For details, see
[LICENSE](LICENSE). Content adapted from Google documentation remains under
the Creative Commons Attribution 4.0 License.
