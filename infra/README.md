# infra

One-time Google Cloud setup for MANIFEST. The decisions behind it are in
[ADR 0001](../docs/adr/0001-cloud-identity-and-shared-resources.md).

## Run it

You need `gcloud` signed in as the project owner (`gcloud auth login`) and the
beta component (`gcloud components install beta`).

1. Preview every command without changing anything:

   ```sh
   PROJECT=<project-id> DRY_RUN=1 ./infra/setup.sh
   ```

2. Apply. The script shows the account, project and billing account, and asks
   before it changes anything:

   ```sh
   PROJECT=<project-id> ./infra/setup.sh
   ```

3. Run the `gh variable set` commands the script prints.

4. Add the Gemini key. Copy it in AI Studio, then run the `pbpaste` command the
   script prints, and clear the clipboard with `pbcopy < /dev/null`. The key goes
   from the clipboard to Secret Manager and is never written to a file. Check with
   `gcloud secrets versions list gemini-api-key`, which shows versions, never values.

The script is idempotent. Re-run it after adding a repository to
`GITHUB_REPOS`.

## What it creates

| Resource | Name | Free Tier note |
|---|---|---|
| WIF pool and provider | `github-tw` / `github-siddharthraop` | Free |
| Deploy service account | `tw-deploy` | Free |
| Runtime service account | `manifest-run` | Free |
| Firestore | `(default)`, us-central1 | Only this database gets the free quota |
| Bucket | `gs://<project>-manifest-reports` | 5 GB-months in us-central1 |
| Artifact Registry | `tw-images`, keep latest | 0.5 GB |
| Secret | `gemini-api-key` (no value) | 6 versions |
| Budget | `tech-writing-portfolio-5usd` | Alerts at 20, 50, 100% |
| BigQuery quota | `QueryUsagePerDay` = 6 GiB | 1 TiB a month |

## Deploys

`.github/workflows/deploy.yml` runs on every push to `main`: it builds the image, pushes it to
`tw-images`, runs [`deploy.sh`](deploy.sh) as `tw-deploy` (keyless, through WIF) and executes one
migrate run as a smoke test. `deploy.sh` defines both Cloud Run resources (ADR 0008):

| Resource | Name | Shape | Free Tier note |
|---|---|---|---|
| Cloud Run job | `manifest-migrate` | 1 task, no retries, 2 vCPU / 4 GiB, 30 min limit | Jobs: 240,000 vCPU-s and 450,000 GiB-s a month |
| Cloud Run service | `manifest-api` | 0 to 1 instance, request-based billing, 1 vCPU / 1 GiB | 180,000 vCPU-s, 360,000 GiB-s, 2 million requests |

Optional repo variables for sign-in (without them the API is read-only):
`MANIFEST_OAUTH_CLIENT_ID` (a Google OAuth web client ID) and `MANIFEST_ALLOWED_EMAILS`
(comma-separated). The console's build step sets these up.

## After the first deploy

`tw-deploy` can't change IAM, so grant these once, yourself. The first makes the API readable by
anyone (writes still need sign-in); the second lets the API start the migration job, and only
that job.

```sh
gcloud run services add-iam-policy-binding manifest-api \
  --region=us-central1 --member=allUsers --role=roles/run.invoker
gcloud run jobs add-iam-policy-binding manifest-migrate --region=us-central1 \
  --member="serviceAccount:manifest-run@$(gcloud config get-value project).iam.gserviceaccount.com" \
  --role=roles/run.jobsExecutorWithOverrides
```

Check it worked: `curl "$(gcloud run services describe manifest-api --region=us-central1 --format='value(status.url)')/api/runs"`
lists the smoke run.
