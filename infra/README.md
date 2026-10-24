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

4. Add the Gemini key with the `read -rs` command the script prints. The key goes
   from your keyboard to Secret Manager and is never written to a file.

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

## After the first deploy

`tw-deploy` can't change IAM, so make the API public once, yourself:

```sh
gcloud run services add-iam-policy-binding manifest-api \
  --region=us-central1 --member=allUsers --role=roles/run.invoker
```
