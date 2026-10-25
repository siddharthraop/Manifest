# ADR 0004: Check each topic against DITA-OT's own output

- Status: Accepted
- Date: 2026-10-24
- Deciders: Siddharth Rao

## Context

Design doc §5.2 defines `check_topic`: counts, text ("builds the source with DITA-OT HTML5 and
the output with Sphinx for the same DITAVAL file, then compares the text") and IDs. US-07's
acceptance criterion is that "the text for each audience matches DITA-OT's output for the same
DITAVAL file". The check is the evidence a writer relies on to approve a migration without
reading every page, so it must be exact and must not be lenient.

## Decision

For every build (unfiltered + one per DITAVAL file), render the original DITA with DITA-OT 4.4
HTML5 and the migrated MyST with Sphinx, then compare each published topic:

1. **Pages.** Both sides publish the same topics.
2. **Text, word for word.** Authored text only: DITA-OT's `<article>` without its `<nav>` and
   generated labels (`Figure 1.`); Sphinx's main area without the `manifest-nav` containers
   and heading permalinks. Block elements separate words; inline elements don't, so markup
   differences (`<span>v5e</span>,`, syntax-highlighted code) don't create false differences.
   Nothing else is normalised. Each difference is reported as lost, added or changed, with the
   words on both sides and the text before it.
3. **Structure.** The same number of headings, tables, table rows, list items, code blocks,
   images and definition terms.
4. **IDs.** Every anchor DITA-OT publishes for the topic (`topicid`, `topicid__elemid`, not
   generated cell IDs) exists in the Sphinx page.

A topic passes only if all four pass in every build.

**Navigation is compared separately** (tool 5). DITA-OT HTML5 groups related links under
"Related concepts/tasks/reference"; its Markdown writer flattens them to "Related information".
That is generated from the map, not authored, so restore marks DITA-OT's generated navigation
as `manifest-nav` (child links to map children, the parent link, related links) and the text
check leaves it out on both sides.

## What the check found on the TPU corpus

The first run passed 112 comparisons except where the migration had really lost something:

| Found | Cause | Fix (mapping rule) |
|---|---|---|
| Figure title missing | DITA-OT's Markdown writer keeps a `<fig>` title only as each image's tooltip | restore: a caption in a `manifest-figure` container |
| `vendor's` became `vendor’s` | Sphinx smart quotes change the text | `smartquotes = False` |
| `Optional:` missing on a step | DITA-OT HTML5 labels `importance="optional"`; Markdown drops it | convert: the label is added as text |
| `--zone=*ZONE*` in inline code | `<varname>` inside `<codeph>` written as literal Markdown | convert: inline markup in `codeph` flattened |

After the fixes: **23/23 topics pass in all 5 builds — 112 comparisons, 0 differences, 0 page
differences, 0 Sphinx warnings.**

## The check is not lenient

Mutation tests break the migrated site and require the check to name each loss: a deleted
sentence (text, lost), a wrong spec value in the substitutions file (text, changed, on every
page that uses it), a removed anchor (ids), a dropped table row (structure + text), a deleted
page (pages) and a removed figure caption (text, lost). All six are caught.

## Consequences

- One full check builds DITA-OT HTML5 and Sphinx five times: about 40 seconds on the TPU map.
  `only_builds` and `reuse_dita_html` keep the mutation tests fast.
- The `<object>` video is not part of the text comparison because DITA-OT drops it on both
  paths; it remains an unhandled item for the writer (US-03), reported by tools 2 and 3.
