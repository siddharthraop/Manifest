# ADR 0005: Links, redirects and the migration report

- Status: Accepted
- Date: 2026-10-24
- Deciders: Siddharth Rao

## Context

Design doc §5.2 tool 5 runs lychee "over the Markdown and the built HTML", checks internal links
offline, writes JSON, and "checks that every old URL has a redirect". US-05 needs every old URL
and anchor to keep working; US-01 needs one report per map that a Docs Platform Lead can approve
from.

## Decisions

1. **lychee runs offline with fragment checking on each Sphinx site** (`--offline
   --include-fragments --format json`), reusing the HTML `check_topic` built. External links are
   listed, not fetched, so the result is deterministic and CI-safe. lychee is pinned (0.24.2).
2. **Navigation is compared with DITA-OT, page by page and build by build.** The link targets in
   DITA-OT's `<nav>` must equal those in the page's `manifest-nav` containers.
3. **Generated links follow the map relationship, as DITA-OT decides it.** The navigation check
   found 36 differences on its first run, all on pages a relationship table keeps published in
   builds that filter them out of the TOC: DITA-OT drops their family-sibling links there.
   Restore now gives each generated link the builds where its relationship holds: a reltable
   link wherever its target is published; a map-tree link (parent, child, family sibling) only
   where both pages are in the TOC. After the fix: 0 differences on 112 page-builds.
4. **Old URLs: same path, or a redirect.** Sphinx keeps DITA-OT's page paths
   (`topics/x.html`) and restore keeps DITA-OT's anchors (`topicid__elemid`), so on the TPU map
   no redirect is needed. `redirects.json` is still written for each map; when a page does move,
   `check_links` writes a static redirect page (meta refresh plus a script that keeps
   `#anchor`), because GitHub Pages has no server-side redirects, and verifies the old URL and
   its anchors through it. Unresolvable URLs are listed by name.
5. **One report per map: `report.json` and `report.html`**, self-contained, in the portfolio's
   design tokens with fonts self-hosted (SIL OFL). Each topic is Pass, Review or Fail; each
   difference is listed under its topic with the check and build that found it; review items
   (US-03), findings about the source, and the design doc §7 metrics measured on the run.
   Status is never shown by colour alone.
6. **`manifest migrate MAP` runs everything** and exits non-zero when the gate fails (any check
   difference or link problem). Review items don't fail the gate: they are the writer's queue.

## Result on the TPU map

`manifest migrate corpus/tpu/tpu.ditamap`, about 50 seconds: gate **pass**; 22 topics passed
with no writer needed, 1 sent to a writer (the `<object>` video), 0 failed. 3,110 links checked
across 5 builds, 0 broken, 0 navigation differences, 112 old URLs and 243 old anchors resolve.
Every MVP metric in design doc §7 is met: 0 unreported losses, 100% of topics checked, 100%
of reuse kept, 100% of IDs kept, 0 broken links.
