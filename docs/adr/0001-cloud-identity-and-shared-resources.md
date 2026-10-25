# ADR 0001: Cloud identity and shared resources

- Status: Accepted
- Date: 2026-10-24
- Deciders: Siddharth Rao

## Context

MANIFEST is the first of six tech-writing builds that share one Google Cloud
project and one billing account with about 30 other projects. The Free Tier is
calculated per billing account, so the six builds may use at most about 20% of
each limit ([Free Tier](https://docs.cloud.google.com/free/docs/free-cloud-features)).
Google Cloud credentials stay with the project owner and GitHub Actions' keyless identity, and no project ID goes in code.

## Decisions

1. **Keyless deploys through Workload Identity Federation, via a service
   account.** GitHub Actions impersonates `tw-deploy`. We use the
   service-account variant rather than direct federation because a Cloud Run
   deploy must act as the runtime service account, and that needs a service
   account in the chain
   ([google-github-actions/auth](https://github.com/google-github-actions/auth)).

2. **Admission by numeric GitHub IDs, not names.** The provider admits only
   tokens whose `repository_owner_id` is `50285656`, and each repository is bound
   by `repository_id`. Names can be re-registered after deletion; numeric IDs are
   never reused
   ([security considerations](https://github.com/google-github-actions/auth/blob/main/docs/SECURITY_CONSIDERATIONS.md)).
   Adding a build means adding one `name:id` pair to `GITHUB_REPOS` in
   `infra/setup.sh` and re-running it.

3. **Least privilege.** `tw-deploy` gets `run.developer` (deploy, but not change
   IAM), `artifactregistry.writer` on one repository, and `serviceAccountUser` on
   `manifest-run` only. `manifest-run` gets `datastore.user`, `storage.objectAdmin`
   on the report bucket only, and `secretAccessor` on the Gemini secret only.
   Consequence: making the API public (`allUsers` invoker) is a one-time manual
   step by the owner after the first deploy, not something CI can do.

4. **Shared Firestore `(default)` database, namespaced collections.** Only one
   database per project qualifies for the free quota
   ([Firestore quotas](https://cloud.google.com/firestore/quotas)), so all six
   builds use `(default)` and prefix collections by product (`manifest_runs`,
   `manifest_rules`, `manifest_reviews`, `manifest_feedback`).

5. **One shared image repository with a keep-latest cleanup policy**, which
   holds Artifact Registry under 20% of its 0.5 GB allowance.

6. **Cost guards.** A budget of about USD 5 (SGD 6.40 on this SGD billing account;
   the amount must be in the account's currency) measured before credits, so alerts reflect real
   usage while trial credit pays for it; a BigQuery `QueryUsagePerDay` quota of
   6 GiB (186 GiB a month, under 20% of 1 TiB). Cloud Run has no quota for this,
   so its ceiling is held by `max-instances=1`, no minimum instances, and
   request-based billing, set in the deploy definitions.

## Consequences

- No long-lived keys exist anywhere; a leaked repository cannot deploy unless its
  numeric ID is bound.
- Setup is one idempotent script that the owner runs; everything else rebuilds
  from Git.
- The billing kill switch is deliberately not installed while the account is a
  Free Trial, which cannot bill.
