# Deploying

Everything deploys from `main` without keys: GitHub's OIDC token is exchanged through Workload Identity Federation for
the `tw-deploy` service account ([ADR 0001](../adr/0001-cloud-identity-and-shared-resources.md)).

| Workflow | Runs on | Does |
| --- | --- | --- |
| `ci.yml` | Every pull request and push to `main` | Lint, types, tests, help gate, container |
| `deploy.yml` | Push to `main` (not docs-only changes) | Image → Artifact Registry, `infra/deploy.sh`, one migrate smoke run |
| `pages.yml` | Push to `main` | `site/build.sh` → GitHub Pages |

One-time setup is `infra/setup.sh` and the two IAM grants in [`infra/README.md`](../../infra/README.md). Sign-in needs two
repository variables, `MANIFEST_OAUTH_CLIENT_ID` and `MANIFEST_ALLOWED_EMAILS`; without them the API is read-only.

## The Gemini key

Live runs read `GOOGLE_API_KEY` from the `gemini-api-key` secret, inside the job only. To add or rotate it, on a machine
signed in to gcloud: paste `pbpaste | gcloud secrets versions add gemini-api-key --data-file=-` into a terminal, copy the
key, then press Enter. Never paste a key into a chat, an issue or a commit.
