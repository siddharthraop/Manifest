# ADR 0007: The fix agent: ADK, Gemini Flash, two fixes, the checks decide

- Status: Accepted
- Date: 2026-10-24
- Deciders: Siddharth Rao

## Context

Design doc §5.3: when `check_topic` reports a difference, Gemini reads the difference and the
source and proposes a fix; the agent runs the check again; after two failed fixes it opens a
review issue; an approved fix becomes a mapping rule, so the next map doesn't call Gemini.
ADR 0006 defined a fix as a validated XSLT rule. This ADR decides how the agent runs.

Constraints from the project: free or open-source tools on Google Cloud; the Gemini API free
tier is for public content only, because free-tier content may be used to improve Google
products ([Gemini API terms](https://ai.google.dev/gemini-api/terms)); MANIFEST never handles or
stores the API key; CI and the public demo must not spend quota.

## Decision

1. **Google's Agent Development Kit (ADK, Apache 2.0) runs the loop**
   ([ADK docs](https://google.github.io/adk-docs/)): one `LlmAgent` per failing topic with an
   in-memory runner. It is Google's open-source framework for Gemini agents, so the stack
   stays Google Cloud plus open source, and it moves to Vertex AI Agent Engine later without a
   rewrite. It is an optional extra (`pip install 'manifest-dita[agent]'`); `manifest migrate`
   doesn't need it.
2. **The model has three tools and nothing else:** `read_failure` (the differences, the DITA
   source, DITA-OT's HTML5 rendering and the migrated MyST, each clipped), `try_fix` (one
   complete rule) and `open_review`. It never writes a file, runs a command or sees a check it
   could edit.
3. **The limits are code, not prompt.** `try_fix` validates the rule (ADR 0006), converts,
   restores and re-checks the topic in every build it appears in, and also re-checks every
   other page, so a rule that breaks another topic fails. A third `try_fix` is refused. An
   unsafe or invalid rule, or one that crashes the pipeline, counts as a failed attempt. The
   session, not the model, decides the outcome: "fixed" only if an attempt passed; otherwise
   a review file is written even if the model never called `open_review`. A test shows that a
   model claiming success doesn't change the result.
4. **A passing rule is *proposed*, never applied silently.** It is saved to
   `build/agent/proposed/` with provenance (run, topic, model, attempt). The report shows the
   topic as "✎ Fixed" until a writer runs `manifest rules approve FILE --by NAME`, which copies
   it into `rules/` as approved. From then on `manifest migrate` applies it without Gemini,
   which is the economic point: the model is paid for once per new kind of element, not once
   per topic.
5. **Model: `gemini-3.8-flash`, temperature 0**, with `gemini-3.5-flash` as the documented
   fallback (`--model`). Flash is the tier on the free plan with enough context for a topic,
   its HTML5 and its MyST. The 3.8 model is stable but listed by Google as a short-term
   availability model, while 3.5 Flash has a published shutdown date no earlier than
   2027-05-19 ([Gemini models](https://ai.google.dev/gemini-api/docs/models),
   [deprecations](https://ai.google.dev/gemini-api/docs/deprecations)). The model ID is a
   flag, so a change is a one-line config edit plus a new recording.
6. **Three model modes.** *live* calls Gemini with the key from `GOOGLE_API_KEY`, which the
   writer reads from Secret Manager into the environment for one command; every response is
   recorded to JSONL. *replay* plays a recording back: CI and the public demo use it at no
   cost and with no key. *scripted* is a list of steps written in a test. In every mode the
   tools and checks run for real; only the model's words are fixed. A replay that diverges
   from its recording stops with an error instead of improvising.

## Consequences

- The agent can't make a topic pass by saying so, by touching the checks, or by breaking
  another topic. The worst a bad model can do is waste two attempts and send a topic to a
  writer, which is what happens without an agent.
- On the TPU map, a scripted session reproduces the design doc's flow: a rule for the choice
  table alone fails the structure check (the topic also has a hazard statement), the second
  rule covers both and passes, the gate passes, and after approval `manifest migrate` passes
  with no agent. The hand-written rules stand in for Gemini until the first live recording.
- Reviews are Markdown files in `build/agent/reviews/`; `--open-issues` also files them as
  GitHub issues when `GITHUB_TOKEN` and `GITHUB_REPOSITORY` are set (as in Actions).
- Not yet: running the agent in Cloud Run (build step 4) and a recorded live session in CI.
