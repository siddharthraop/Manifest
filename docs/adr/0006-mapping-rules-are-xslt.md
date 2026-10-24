# ADR 0006: Mapping rules are validated XSLT templates

- Status: Accepted
- Date: 2026-10-24
- Deciders: Siddharth Rao

## Context

Design doc §5.3: when `check_topic` reports a difference, Gemini proposes a fix; after two
failed fixes the agent opens a review issue; an approved fix becomes a mapping rule, so the
next map doesn't call Gemini. The design doesn't say what a "fix" or a "rule" is.

Every topic in the TPU set migrated cleanly, so the agent had nothing to do. The corpus now has
`tpu-v5e-slice-planning` (dita-corpus PR #1), a task with a `<choicetable>` and a
`<hazardstatement>`. With no rule, DITA-OT's Markdown writer turns the hazard statement into a
bullet list under "CAUTION:", where HTML5 shows a two-row table, and the choice table's ID has
no text to carry an anchor. The check fails the topic on text, structure and IDs.

## Decision

1. **A rule is an XSLT 1.0 template** that rewrites one kind of DITA element into DITA that
   DITA-OT's Markdown writer handles, stored as JSON with an ID, the element it matches, a
   description, a status (proposed, approved, rejected) and its provenance. XSLT is what DITA
   writers already use for DITA-OT plug-ins, so a rule is something a writer can read and
   review, and its behaviour is exact.
2. **Rules run on convert_map's working copy, never on the source.** The checks still compare
   against DITA-OT HTML5 of the untouched source, so a rule can only make a topic pass by making
   the migrated page say what the published page says. The DOCTYPE is written back after the
   transform, because DITA-OT needs it to give each element its DITA class.
3. **Because a model writes rules, they are held to strict limits before they run:** only
   top-level `<xsl:template match>`; no import, include or output control; no `document()`,
   `unparsed-text()` or `system-property()`; no namespaces other than XSLT's own (so no
   extension functions); and lxml's XSLT access control denies every file and network
   operation. A test proves the access control alone refuses a file read even if validation
   were bypassed.
4. **Only approved rules run** (`manifest migrate --rules rules`). Hand-written rules used by the
   tests live in `tests/fixtures/rules/` and are labelled as stand-ins.

## Found while proving a rule can fix the topic

Two hand-written trial rules (choice table → simple table; hazard statement → a two-row simple
table) made the topic pass only after fixing one more DITA-OT Markdown writer defect: when a
table cell has several paragraphs it writes a raw HTML table and then the next block on the
same line (`</table>1.  Find the…`), so Markdown swallows the whole step list. Restore now
splits it. The run also exposed two checker bugs, now fixed: an inline SVG icon's metadata was
read as text, and generated header-cell IDs were treated as source anchors (expected anchors
now come from IDs in the source).

## Result

- No rules: 23 of 24 topics pass; the slice-planning topic fails on text, structure and IDs,
  and its choice-table ID is a review item. Exactly what the agent exists for.
- With the two fixture rules: gate pass, 23 topics pass and 1 goes to a writer (the video),
  every design doc §7 metric met.
