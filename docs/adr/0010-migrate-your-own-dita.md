# ADR 0010: Anyone can migrate their own DITA

- Status: Accepted
- Date: 2026-10-25
- Deciders: Siddharth Rao

## Context

Success, in the product owner's words: an end user takes a set of DITA files, runs them through MANIFEST, and receives
docs-as-code files and a report, with the design's promise kept: no unreported loss. The deployment was read-only.

## Decision

1. **Upload a .zip, no sign-in** (`POST /api/uploads`, console screen *Migrate your DITA*). The job unpacks it, runs the
   same five tools and checks as every other run (no agent, so no model quota), and stores `result.zip`: the MyST
   Sphinx project, the report, the redirects, and a README that lists every topic that isn't clean and why.
2. **The promise is kept by naming, not hiding.** Every topic is checked in every build; a topic that differs is still
   delivered, but the README and report say which check failed. A run that can't complete fails with its reason.
3. **Uploads are untrusted XML.** Limits: 10 MB zipped, 60 MB unpacked, 2,000 files. No path may leave the unpack
   folder. Only DITA, XML, images and text are unpacked. A DOCTYPE must be a public OASIS DITA DTD without an internal
   subset, and no file may declare entities, so XML external entities can't read files or URLs (the metadata server
   included) into the output. Files are kept with the run so its report can be read.
4. **Free tier.** Public runs share one queue (one at a time) and a daily limit (`MANIFEST_PUBLIC_RUNS_PER_DAY`,
   default 10). A migrate run uses about 300 vCPU-seconds, so 10 a day stays inside the 20% share most months.
   Live agent runs and rule decisions still need a signed-in, allowlisted writer.
5. **Live progress.** The job records each stage (unpack, inventory, convert, restore, check, links, report); the run
   page refreshes until it finishes. Run IDs gained a random suffix so two runs started in the same second can't collide.
