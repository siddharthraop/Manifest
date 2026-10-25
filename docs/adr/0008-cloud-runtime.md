# ADR 0008: The cloud runtime: one image, a Cloud Run job, a scale-to-zero API

- Status: Accepted
- Date: 2026-10-25
- Deciders: Siddharth Rao

## Context

Build step 4 (handoff §4, §8): a Cloud Run job runs migrations and a Cloud Run service serves an
API, both deployed by `deploy.yml` through Workload Identity Federation; reports go to Cloud
Storage and runs to Firestore. Constraints: nothing always on; the six tech-writing products
together may use at most about 20% of each free-tier limit; no keys in code; every write audited;
"Run live" protected so the public can't spend the Gemini quota.

## Decision

1. **One image for both.** The job runs `python -m manifest.cloud`; the service runs
   `uvicorn --factory api.main:create_app_from_env`. The API reads the corpus to list maps and
   shares the engine's code, and one stored image costs half of two.
2. **Make the image small, because storage is the scarce free resource.** Artifact Registry gives
   0.5 GiB free per billing account ([pricing](https://cloud.google.com/artifact-registry/pricing)),
   so the 20% share is about 100 MB. The toolchain is large, so each cut was proved by running the
   full TPU migration on the cut-down tools (gate PASS, 23 of 24, in 56 s on the build box):
   - Java: a `jlink` runtime with the 21 modules DITA-OT 4.4 uses: 59 MB on disk, 39 MB
     compressed, instead of a full JRE.
   - DITA-OT: the four PDF renderer plug-ins are uninstalled (`org.dita.pdf2` stays, because
     `org.dita.index` depends on it). That leaves 69 MB on disk, 39 MB compressed.
   - Python: runtime extras only, no pip, no bytecode, Babel locale data for English only.
     Before the Babel cut this was 48 MB compressed.
   - DITA-OT and lychee are downloaded from their GitHub releases and checked against SHA-256.

   The result measured in CI is 171 MB compressed (210 MB before DITA-OT's owner was set at copy
   time instead of by a `chown -R` layer that stored it twice). That is still about 1.7 times the
   share. We accept it: the overage is at most 0.07 GiB × $0.10 = under $0.01 a month, and it is billed only
   if the whole billing account goes over 0.5 GiB. CI fails above 200 MB, so the image can't creep,
   and the repository keeps only the latest version.
3. **Job shape: 1 task, no retries, 2 vCPU / 4 GiB, 30-minute limit.** Cloud Run's filesystem is
   in memory and a fix run writes about 85 MB beside DITA-OT's JVM and Sphinx, so 4 GiB leaves
   headroom. Jobs get 240,000 vCPU-seconds and 450,000 GiB-seconds free a month
   ([Cloud Run pricing](https://cloud.google.com/run/pricing)). The 20% share is 48,000 vCPU-s and
   90,000 GiB-s, which is about 60 agent runs of 6 minutes, or about 190 migrate runs of 2 minutes,
   a month. Retries are off because a failed run should be read, not repeated.
4. **API shape: 0 to 1 instance, request-based billing (`--cpu-throttling`), 1 vCPU / 1 GiB,
   60 s timeout.** Request-based services get 180,000 vCPU-s, 360,000 GiB-s and 2 million requests
   free a month (same source). No min instances, so it costs nothing when idle.
5. **Storage.** Firestore `(default)` holds `runs`, `rules`, `reviews` and `audit`. Each write is
   committed with its audit record in one batch, so neither exists without the other. Cloud Storage
   holds only what the console needs from a run: report, agent record, MyST pages, reuse ledger,
   inventory. That is under 5 MB a run (a test enforces it) against the 5 GB-month free storage.
   The built sites stay out. The bucket has public access prevention on, so the API serves report
   files itself, HTML with a CSP that allows no scripts.
6. **Who may do what.** Anyone may read maps, runs, reports, rules and reviews (public sample
   content). Starting a run, approving or rejecting a rule, and reading the audit trail need a
   **Sign In With Google ID token**. The API verifies it as Google documents (signature, `aud`,
   `exp`, plus `iss` and `email_verified`) and checks the address against `MANIFEST_ALLOWED_EMAILS`
   ([verify a Google ID token](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token)).
   We chose this over Firebase Auth or Identity Platform because there is no user store to run:
   the console needs one Google button and the API needs one allowlist. Identity Platform would be
   free at this scale (50,000 social-sign-in MAU, [pricing](https://cloud.google.com/identity-platform/pricing)),
   but it adds a product to configure and secure for nothing we use. Without a client ID and an
   allowlist the API is read-only (503 on writes), which is the safe default.
7. **Quota protection.** One run at a time: the API refuses a new run while one is queued or
   running. Live mode needs a signed-in allowlisted user. The Gemini key reaches only the job,
   through `--set-secrets` from Secret Manager, never the API.
8. **Least privilege, and the IAM the deployer can't grant.** `tw-deploy` (`run.developer`)
   deploys and can run the smoke job, but it can't change IAM
   ([Cloud Run IAM roles](https://docs.cloud.google.com/run/docs/reference/iam/roles)). Siddharth
   grants two bindings once (`infra/README.md`): `allUsers` → `run.invoker` on the API, and
   `manifest-run` → `run.jobsExecutorWithOverrides` on the job only. That is the narrowest role
   that can start a job with settings.
9. **Deploys from `main` only**, one at a time, image pinned by digest, then one migrate run
   (no Gemini) as a smoke test that also leaves a real run for the console.

## Consequences

- The deployed job, the API and the stored run shape are covered by tests that use the same
  interfaces with in-memory fakes, plus job tests on the real corpus. CI builds the image on every
  PR, migrates the TPU map inside it and starts the API.
- `deploy.yml` can't run until the stacked PRs reach `main`.
- The console (step 5) will add the Google OAuth web client and set the two repo variables.
