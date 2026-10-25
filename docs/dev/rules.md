# Writing a mapping rule

A rule is a JSON file in `rules/` with an XSLT 1.0 template that rewrites one element before conversion
([ADR 0006](../adr/0006-mapping-rules-are-xslt.md)). Only rules with `"status": "approved"` run.

```json
{
  "id": "table-title-to-paragraph",
  "match": "table",
  "status": "approved",
  "description": "One sentence a writer can review.",
  "xslt": "<xsl:template match=\"table[title]\">…</xsl:template>",
  "provenance": {"proposed_by": "…", "approved_by": "…"}
}
```

Keep every piece of text and every `@id`, and keep the output valid DITA: DITA-OT parses the working copy in strict mode.
Templates only; `document()`, imports, includes and extension functions are refused, and lxml's access control denies all
file and network access. Run `manifest migrate` on the affected map: the checks tell you whether the rule works.
