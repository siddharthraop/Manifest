# ADR 0009: Console, online help and the site

- Status: Accepted
- Date: 2026-10-25
- Deciders: Siddharth Rao

## Context

Build steps 5 and 6 (handoff §5, §6): a console with six screens and help on every one, a DITA help set with
context-sensitive help (CSH), a help center, and MANIFEST migrating its own help. Steps 7: ship v1.0.0.

## Decision

1. **Console: a static page with no framework** (`console/`, about 500 lines of JavaScript). Six screens, hash routing,
   every API value escaped, Siddharth's tokens only, light and dark themes, keyboard and screen-reader support. A
   framework would add a build step and a dependency tree for six read-mostly screens.
2. **Sign-in with Google Identity Services** in the browser; the API verifies the ID token (ADR 0008). Without a client
   ID the console says it is read-only.
3. **CSH lives with the help, not the UI.** Each topic carries `<resourceid appname="manifest-console" appid="…"/>`;
   `help/tools/build_csh.py` writes `csh.json` (title, short description, first steps, related topics from the
   relationship table). The panel opens from "?" or the `?` key. **The help gate**: a test fails if any help ID in the
   console has no topic, and CI builds the help in DITA-OT strict mode.
4. **The API reference is generated** from FastAPI's OpenAPI document into a DITA reference topic
   (`help/tools/openapi2dita.py`, the corpus's `api2dita` approach); CI fails if it is stale.
5. **The site is built by a workflow** (`site/build.sh`, `pages.yml`): landing page, design document, console, help
   center, and both migrations with their reports. Nothing generated is committed.
6. **MANIFEST migrates its own help, and the gate must pass in CI.** The first run found four cases the TPU corpus never
   exercised; all are fixed, each with a test:
   - source text containing `@@` could be read as a placeholder (now escaped before tokens go in);
   - a `|` in a table cell ended the Markdown cell (now escaped on table rows);
   - a one-step task is a paragraph in DITA-OT HTML5 but a list in Markdown (now normalised to `steps-informal`);
   - table titles are dropped by DITA-OT's Markdown writer (an approved rule, `rules/table-title-to-paragraph.json`).
   One key reference on a link has no rule yet and correctly goes to a writer as a review item.
7. **US-04 is `manifest lint` plus a composite action** (`action/`): seconds, no DITA-OT, annotations with file, line
   and fix on the pull request.

## Consequences

The product's documentation is evidence that the product works. Switching GitHub Pages to "GitHub Actions" is a one-time
repository setting.
