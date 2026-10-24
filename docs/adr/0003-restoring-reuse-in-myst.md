# ADR 0003: Restoring reuse in MyST, and matching DITA-OT per DITAVAL

- Status: Accepted
- Date: 2026-10-24
- Deciders: Siddharth Rao

## Context

Tool 3 turns the placeholders from ADR 0002 back into MyST Markdown for Sphinx (design doc
§5.4). Each MyST behaviour below was checked on Sphinx 9.2 and MyST-Parser 5.1 before the tool
was written, because each one changes the mapping.

| What was tested | Result |
|---|---|
| `{{ product-tpu7x }}` | Fails: Jinja2 reads it as `product` minus `tpu7x`. MyST evaluates substitutions as Jinja2 expressions ([MyST syntax extensions](https://myst-parser.readthedocs.io/en/v5.1.0/syntax/optional.html)). |
| Substitutions in code blocks | Not applied by MyST ("not in code blocks", same page). |
| `{code-block}` with `:substitutions:` and `{substitution-code}` | Work with `{{name}}` (no spaces), from [sphinx-substitution-extensions](https://pypi.org/project/sphinx-substitution-extensions/) (MIT), which does a literal replacement. |
| DITA-OT's heading attributes `# Title {#id .task}` | Not MyST: rendered as literal text. |
| Sphinx labels for `topicid__elemid` | docutils rewrites `__` to `-`, so the HTML ID no longer matches DITA-OT's. |
| `only` blocks with `-t` tags | Work; but Sphinx still resolves links inside an excluded block and warns (the caveat in design doc §5.4). |

## Decisions

1. **Keys become identifier-named substitutions** (`tpu7x-chips-per-pod` →
   `tpu7x_chips_per_pod`), stored once in `_manifest/substitutions.json`. In code blocks and
   inline code they use `{code-block} :substitutions:` and `{substitution-code}`, so no
   keyed value is typed anywhere, including inside `gcloud` commands (US-06).
2. **Each distinct conref target becomes one include file**, named after the shared element
   (`_includes/tpu-warehouse--note-tpu-api-tpu7x.md`). An inline conref (a `ph`) can't be an
   include, so it becomes a substitution.
3. **Conditions are evaluated with DITAVAL semantics for every build** (unfiltered + one per
   DITAVAL file). A filtered build sets the tags `ditaval` and its file stem, so
   `{only} v5e or not ditaval` reads as "the v5e build and the unfiltered build".
   A condition on a list item or table row is lifted to the whole list or table, with one
   variant per distinct result, so step numbers and rows are right in every build.
4. **Anchors use DITA-OT HTML5's own form, `topicid__elemid`**, as raw HTML
   (`<span id="…"></span>`), because no Sphinx label can produce that ID. Old bookmarks keep
   working (US-05). IDs of shared elements are dropped, because DITA-OT HTML5 doesn't publish
   them either (checked: 0 occurrences in its output).
5. **Pages follow DITA-OT exactly.** A page is left out of a build only if no visible
   reference reaches it; it is left out of the table of contents where only a reltable
   reaches it. DITA-OT's generated links (parent, child, related) to a page a build doesn't
   publish are cut by a `source-read` hook in `conf.py` before Sphinx parses the page,
   avoiding the `only` caveat. Authored conditions keep the standard `{only}` directive.
6. **What the source does wrong is reported, not silently copied:** `findings` in
   `_manifest/restore.json`.

## Findings on the TPU corpus

- **The reltable keeps filtered topics published (16 findings).** The map scopes each
  generation's branch with `@platform`, but the relationship table references the same topics
  with no condition, so DITA-OT 4.4 still builds the v5p, v6e and TPU7x pages in the v5e build
  (they're only missing from the TOC). MANIFEST matches that output and tells the writer how
  to fix it: add the condition to the reltable row.
- **19 possible typed copies.** Text such as `256` or `16x16x24` in configuration tables equals
  a key's value. The migration keeps exactly the same count it found in the source (tested),
  and lists each place for a writer to decide.

## Result

All 5 builds (unfiltered, v5e, v5p, v6e, TPU7x) complete with zero Sphinx warnings (`-W`),
publish the same pages as DITA-OT 4.4, and keep all 51 published IDs as anchors in DITA-OT
form. 169 key references → substitutions; 11 conrefs → 5 include files and 1 substitution;
11 conditions → `only` blocks.
