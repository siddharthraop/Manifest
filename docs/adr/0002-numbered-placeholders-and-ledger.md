# ADR 0002: Numbered placeholders and a ledger

- Status: Accepted
- Date: 2026-10-24
- Deciders: Siddharth Rao

## Context

The design doc (§5.2) has `convert_map` put a placeholder in place of each conref, key
reference and conditional block before running DITA-OT, and marks one assumption to test
first: that placeholders pass through DITA-OT unchanged.

A plain DITA-OT 4.4 `markdown` build of the TPU corpus shows why the placeholders are needed:
keys arrive as typed copies (`--version=v2-alpha-tpuv5-lite`), conrefs are pasted in, all four
generations' conditional steps are merged, figure and element IDs are dropped, and the
transceiver topic's `<object>` video disappears with no warning.

A spike carried descriptive placeholders (`@@CONREF:../reuse/tpu-task-warehouse.dita#…@@`)
through DITA-OT. They survived in headings, table cells and code blocks, but DITA-OT's
Markdown writer escaped characters inside them (`#` became `\#`).

## Decision

1. **Tokens are short and numbered:** `@@K12@@` (key), `@@C3@@` (conref), `@@B7@@`…`@@E7@@`
   (condition begin and end), `@@I9@@` (element ID). They use only `@`, capital letters and
   digits, which DITA-OT does not escape. What each token means is stored in `ledger.json`,
   with the source file, element and line.
2. **Every token belongs to one output file** and must appear in it exactly once. `convert_map`
   counts them after the build; a missing or repeated token fails that file. The single
   allowed exception is keys in a topic's title or short description, which DITA-OT copies
   into other topics' link text by design; those are reported as `via_link_text`.
3. **One include per distinct conref target**, converted from the original source element in
   the smallest valid wrapper (a task with one step for steps, a topic body otherwise), so
   shared content is converted once.
4. **Anything that can't be carried is an `unhandled` ledger entry**, never a silent drop:
   `<object>`, conref ranges and push, keyrefs on elements with content.
5. **Markers go only into block-level text** (`p`, `cmd`, `entry`, `note`, `codeblock`, …),
   never into inline elements, never into an element about to be replaced by a conref token,
   and never into a figure title.

## Lessons from the first run on the real corpus

The count check failed 21 of 29 files on its first run. All three causes were real:

- **A condition's end marker went inside the last `<keyword>` of a code block.** That gave the
  keyword content, DITA-OT resolved the key, and the marker was lost. → rule 5, inline.
- **A step's end marker went inside a conref'd note**, which the conref replacement then
  cleared. → rule 5, conref.
- **DITA-OT repeats a figure title onto every image in the figure**, so an ID marker there
  appeared twice. → rule 5, figure title.

After the fixes: 29 of 29 files keep every placeholder (214 tokens: 169 keys, 11 conrefs,
11 conditions, 12 IDs; 6 includes). The only unhandled element is the `<object>` video.

## Consequences

- `restore_reuse` (tool 3) works from the ledger, not from parsing placeholder text.
- A conref'd note renders as `**Note:** @@C5@@` even when the shared note is `type="important"`;
  restore must replace the whole paragraph, label included, with the include.
- Each regression above has a unit test in `tests/test_convert.py`.
