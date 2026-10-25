#!/usr/bin/env bash
# Deploy MANIFEST's Cloud Run job and API service from one image (build step 4, ADR 0008).
#
# Run by .github/workflows/deploy.yml as the tw-deploy service account (keyless, through Workload
# Identity Federation). It can also be run by hand by someone signed in to gcloud with the same
# roles. Every value comes from the environment; no project ID lives in this file.
#
#   PROJECT REGION RUNTIME_SA BUCKET IMAGE      required
#   OAUTH_CLIENT_ID ALLOWED_EMAILS CORS_ORIGINS optional: without the first two the API is read-only
#
# What is never set here (the frugal rules in the foundation doc): min instances above 0,
# instance-based billing for the API, more than one task, retries, or public access. Public read
# access to the API is granted once by hand (infra/README.md); tw-deploy can't change IAM.
#
# Flags checked against `gcloud run deploy --help` and `gcloud run jobs deploy --help`
# (Google Cloud SDK 570.0.0). Sizing and cost: ADR 0008.
set -euo pipefail

: "${PROJECT:?}" "${REGION:?}" "${RUNTIME_SA:?}" "${BUCKET:?}" "${IMAGE:?}"
OAUTH_CLIENT_ID="${OAUTH_CLIENT_ID:-}"
ALLOWED_EMAILS="${ALLOWED_EMAILS:-}"
CORS_ORIGINS="${CORS_ORIGINS:-https://siddharthraop.github.io}"
JOB=manifest-migrate
SERVICE=manifest-api
LABELS=project=manifest,managed-by=deploy-sh
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

echo "Job $JOB: one task, no retries, 2 vCPU / 4 GiB, 30 min limit"
gcloud run jobs deploy "$JOB" --project="$PROJECT" --region="$REGION" --image="$IMAGE" \
  --service-account="$RUNTIME_SA" \
  --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=30m \
  --cpu=2 --memory=4Gi \
  --set-env-vars="MANIFEST_BUCKET=${BUCKET}" \
  --set-secrets="GOOGLE_API_KEY=gemini-api-key:latest" \
  --labels="$LABELS"

echo "Service $SERVICE: 0 to 1 instance, request-based billing, 1 vCPU / 1 GiB"
# ^|^ makes | the separator, so the comma-separated lists (and the @ in emails) stay intact
# (`gcloud topic escaping`).
gcloud run deploy "$SERVICE" --project="$PROJECT" --region="$REGION" --image="$IMAGE" \
  --service-account="$RUNTIME_SA" \
  --min-instances=0 --max-instances=1 --cpu-throttling --concurrency=20 --timeout=60 \
  --cpu=1 --memory=1Gi --port=8080 \
  --command=uvicorn \
  --args=--factory,api.main:create_app_from_env,--host,0.0.0.0,--port,8080 \
  --set-env-vars="^|^MANIFEST_BUCKET=${BUCKET}|MANIFEST_JOB=projects/${PROJECT}/locations/${REGION}/jobs/${JOB}|MANIFEST_OAUTH_CLIENT_ID=${OAUTH_CLIENT_ID}|MANIFEST_ALLOWED_EMAILS=${ALLOWED_EMAILS}|MANIFEST_CORS_ORIGINS=${CORS_ORIGINS}" \
  --labels="$LABELS"

gcloud run services describe "$SERVICE" --project="$PROJECT" --region="$REGION" --format='value(status.url)'
