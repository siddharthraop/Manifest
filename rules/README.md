# Approved mapping rules

Each rule here is an XSLT template, stored as JSON with its provenance: the run that proposed
it, the topic it fixed, the model that proposed it and the writer who approved it. `manifest
migrate --rules rules` applies the approved ones; the next map that has the same element
migrates without calling Gemini.

Rules arrive here one way: Gemini proposes a fix for a topic that fails a check, the checks
confirm it, and a writer approves it (build step 3). Hand-written examples used by the tests
live in `tests/fixtures/rules/` and are not approved rules.

See [ADR 0006](../docs/adr/0006-mapping-rules-are-xslt.md).
