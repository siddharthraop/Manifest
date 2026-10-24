#!/usr/bin/env bash
# =============================================================================
# MANIFEST — one-time Google Cloud setup for the tech-writing portfolio project
# =============================================================================
#
# WHO RUNS THIS: Siddharth, once, on his Mac, signed in with `gcloud auth login`
# as the project owner. Claude never handles GCP credentials (ADR 0001).
#
# WHAT IT CREATES (all idempotent: re-running skips what already exists)
#   1. Required APIs
#   2. Workload Identity Federation pool + GitHub OIDC provider, admitting only
#      repositories owned by GitHub user ID 50285656 (siddharthraop)
#   3. A deploy service account (GitHub Actions impersonates it) and a runtime
#      service account (Cloud Run job + API run as it), least privilege
#   4. The Firestore (default) database and the report bucket in us-central1
#   5. An Artifact Registry Docker repo with a cleanup policy: keep latest only
#   6. An empty Secret Manager secret for the shared Gemini key (you add the
#      value yourself; it is never written to disk or to Git)
#   7. A $5 monthly budget, alerts at 20/50/100%, measured BEFORE credits
#   8. A BigQuery custom daily query quota that holds the 20% Free Tier ceiling
#   9. Labels project=manifest on every resource that supports labels
#
# USAGE
#   PROJECT=project-627597cf-0000-435e-b76 ./infra/setup.sh            # apply
#   PROJECT=project-627597cf-0000-435e-b76 DRY_RUN=1 ./infra/setup.sh  # print only
#
# Every flag below was checked against Google's docs on 24 Oct 2026:
#   Free Tier limits (Cloud Run 180,000 vCPU-s; Storage 5 GB-months in
#     us-central1/us-east1/us-west1; Artifact Registry 0.5 GB; Secret Manager
#     6 versions; BigQuery 1 TiB queries/month; Firestore per project):
#     https://docs.cloud.google.com/free/docs/free-cloud-features
#   WIF pool/provider/binding commands and attribute-mapping syntax:
#     https://github.com/google-github-actions/auth  (README, "through a Service Account")
#   Prefer numeric *_id claims over names (cybersquatting):
#     https://github.com/google-github-actions/auth/blob/main/docs/SECURITY_CONSIDERATIONS.md
#   Deploy roles (run.developer/admin, artifactregistry.writer, iam.serviceAccountUser):
#     https://cloud.google.com/blog/products/devops-sre/deploy-to-cloud-run-with-github-actions
#   Firestore create flags; only one database per project gets the free quota:
#     https://cloud.google.com/sdk/gcloud/reference/firestore/databases/create
#     https://cloud.google.com/firestore/quotas
#   Bucket flags (--uniform-bucket-level-access, --public-access-prevention,
#     --soft-delete-duration; 0 disables soft delete):
#     https://docs.cloud.google.com/sdk/gcloud/reference/storage/buckets/create
#   Artifact Registry cleanup policy JSON (Keep mostRecentVersions + Delete):
#     https://cloud.google.com/artifact-registry/docs/repositories/cleanup-policy
#   Budget flags (--credit-types-treatment=exclude-all-credits = costs before credits;
#     --threshold-rule percent=; --calendar-period; --filter-projects):
#     https://docs.cloud.google.com/sdk/gcloud/reference/billing/budgets/create
#     https://docs.cloud.google.com/billing/docs/reference/budget/rest/v1/billingAccounts.budgets
#   BigQuery custom quota QueryUsagePerDay (default 200 TiB/day, on-demand only):
#     https://docs.cloud.google.com/bigquery/cost-controls
#     https://docs.cloud.google.com/sdk/gcloud/reference/beta/quotas/preferences/create
# =============================================================================
set -euo pipefail

# ---- Inputs (no project ID is hard-coded; ADR 0001) -------------------------
: "${PROJECT:?Set PROJECT to the GCP project ID, e.g. PROJECT=my-project ./infra/setup.sh}"
REGION="${REGION:-us-central1}"          # Free Tier Storage region; foundation rule
LABEL_KEY="project"; LABEL_VAL="manifest"
DRY_RUN="${DRY_RUN:-0}"

# GitHub identity. Numeric IDs, not names (see SECURITY_CONSIDERATIONS above).
GITHUB_OWNER="siddharthraop"
GITHUB_OWNER_ID="50285656"               # gh api repos/siddharthraop/manifest --jq .owner.id
# Repos allowed to impersonate the deploy SA: "name:numeric_repo_id".
# Add the other five builds here as they are created.
GITHUB_REPOS=("Manifest:1431787723")     # gh api repos/siddharthraop/manifest --jq .id

# Shared across the six tech-writing builds (one pool, one deploy SA, one image repo).
POOL_ID="github-tw"
PROVIDER_ID="github-${GITHUB_OWNER}"
DEPLOY_SA_NAME="tw-deploy"
AR_REPO="tw-images"
SECRET_ID="gemini-api-key"

# MANIFEST-specific.
RUNTIME_SA_NAME="manifest-run"
BUCKET="gs://${PROJECT}-manifest-reports"
BUDGET_NAME="tech-writing-portfolio-5usd"
# The budget amount must be in the billing account's own currency (Budget API:
# "it must match the currency of the billing account"), so the value is chosen
# after reading the account's currency in preflight. USD 5 ~= SGD 6.40 at
# 1 USD = 1.278 SGD (Wise mid-market, 24 Oct 2026). Override with BUDGET_AMOUNT=7.00SGD.
# (A case statement, not an associative array: macOS ships bash 3.2.)
budget_for() { case "$1" in USD) echo "5USD" ;; SGD) echo "6.40SGD" ;; *) echo "" ;; esac; }

# 20% of the BigQuery Free Tier (1 TiB/month) is ~205 GiB/month. Custom quotas
# are per day, so 6 GiB/day x 31 days = 186 GiB, safely under the ceiling.
BQ_DAILY_GIB=6

# ---- Helpers ----------------------------------------------------------------
say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
info() { printf '   %s\n' "$*"; }
run()  { if [[ "$DRY_RUN" == "1" ]]; then { printf '   [dry-run]'; printf ' %q' "$@"; echo; } >&2; else "$@"; fi; }
exists() { "$@" >/dev/null 2>&1; }       # true if a describe command succeeds

# ---- Preflight ---------------------------------------------------------------
say "Preflight"
command -v gcloud >/dev/null || { echo "gcloud is not installed"; exit 1; }
ACTIVE_ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)')"
[[ -n "$ACTIVE_ACCOUNT" ]] || { echo "Run: gcloud auth login"; exit 1; }
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
BILLING_ACCOUNT="$(gcloud billing projects describe "$PROJECT" --format='value(billingAccountName)')"
BILLING_ACCOUNT="${BILLING_ACCOUNT#billingAccounts/}"
[[ -n "$BILLING_ACCOUNT" ]] || { echo "Project $PROJECT has no billing account linked"; exit 1; }
BILLING_CURRENCY="$(gcloud billing accounts describe "$BILLING_ACCOUNT" --format='value(currencyCode)')"
if [[ -z "${BUDGET_AMOUNT:-}" ]]; then
  BUDGET_AMOUNT="$(budget_for "$BILLING_CURRENCY")"
  [[ -n "$BUDGET_AMOUNT" ]] || { echo "Billing currency is '$BILLING_CURRENCY'. Re-run with BUDGET_AMOUNT=<amount>${BILLING_CURRENCY} (about USD 5)."; exit 1; }
fi
[[ "$BUDGET_AMOUNT" == *"$BILLING_CURRENCY" ]] || { echo "BUDGET_AMOUNT must end in $BILLING_CURRENCY"; exit 1; }
info "Account:         $ACTIVE_ACCOUNT"
info "Project:         $PROJECT ($PROJECT_NUMBER)"
info "Billing account: $BILLING_ACCOUNT ($BILLING_CURRENCY)"
info "Budget:          $BUDGET_AMOUNT a month (about USD 5)"
info "Region:          $REGION"
if [[ "$DRY_RUN" != "1" ]]; then
  read -r -p "   Apply to this project? [y/N] " reply
  [[ "$reply" == "y" || "$reply" == "Y" ]] || { echo "Stopped."; exit 0; }
fi

DEPLOY_SA="${DEPLOY_SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
POOL_NAME="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}"

# ---- 1. APIs -----------------------------------------------------------------
say "1. Enable APIs"
run gcloud services enable --project="$PROJECT" \
  iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  cloudresourcemanager.googleapis.com serviceusage.googleapis.com \
  run.googleapis.com artifactregistry.googleapis.com \
  firestore.googleapis.com storage.googleapis.com \
  secretmanager.googleapis.com bigquery.googleapis.com \
  billingbudgets.googleapis.com cloudquotas.googleapis.com
# No cloudbuild.googleapis.com: images are built in GitHub Actions (0 Cloud Build minutes).

# ---- 2. Workload Identity Federation ------------------------------------------
say "2. Workload Identity pool and GitHub provider"
if exists gcloud iam workload-identity-pools describe "$POOL_ID" --project="$PROJECT" --location=global; then
  info "Pool $POOL_ID exists"
else
  run gcloud iam workload-identity-pools create "$POOL_ID" --project="$PROJECT" \
    --location=global --display-name="GitHub Actions (tech writing)"
fi

if exists gcloud iam workload-identity-pools providers describe "$PROVIDER_ID" \
     --project="$PROJECT" --location=global --workload-identity-pool="$POOL_ID"; then
  info "Provider $PROVIDER_ID exists"
else
  # Map every claim we assert on; the condition admits only this owner's repos.
  run gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
    --project="$PROJECT" --location=global --workload-identity-pool="$POOL_ID" \
    --display-name="GitHub ${GITHUB_OWNER}" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository,attribute.repository_id=assertion.repository_id,attribute.repository_owner_id=assertion.repository_owner_id,attribute.ref=assertion.ref" \
    --attribute-condition="assertion.repository_owner_id == '${GITHUB_OWNER_ID}'"
fi

# ---- 3. Service accounts and least-privilege roles -----------------------------
say "3. Service accounts"
for sa in "$DEPLOY_SA_NAME:Deploys tech-writing builds from GitHub Actions" \
          "$RUNTIME_SA_NAME:Runs the MANIFEST Cloud Run job and API"; do
  name="${sa%%:*}"; desc="${sa#*:}"
  if exists gcloud iam service-accounts describe "${name}@${PROJECT}.iam.gserviceaccount.com" --project="$PROJECT"; then
    info "$name exists"
  else
    run gcloud iam service-accounts create "$name" --project="$PROJECT" \
      --display-name="$name" --description="$desc"
  fi
done

# Each allowed repo (by numeric ID) may impersonate the deploy SA. Nothing else may.
for entry in "${GITHUB_REPOS[@]}"; do
  repo_id="${entry#*:}"
  run gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" --project="$PROJECT" \
    --role=roles/iam.workloadIdentityUser \
    --member="principalSet://iam.googleapis.com/${POOL_NAME}/attribute.repository_id/${repo_id}" \
    --condition=None --quiet >/dev/null
  info "WIF: repo ${entry%%:*} (${repo_id}) -> $DEPLOY_SA"
done

# Deploy SA: deploy Cloud Run services and jobs (not change IAM), push images
# to one repo only, and act as the runtime SA only (not every SA in the project).
run gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:${DEPLOY_SA}" --role=roles/run.developer --condition=None --quiet >/dev/null
run gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" --project="$PROJECT" \
  --member="serviceAccount:${DEPLOY_SA}" --role=roles/iam.serviceAccountUser --condition=None --quiet >/dev/null
info "Deploy SA: run.developer (project), serviceAccountUser (runtime SA only)"

# Runtime SA: Firestore read/write. Bucket and secret grants are resource-level (below).
run gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:${RUNTIME_SA}" --role=roles/datastore.user --condition=None --quiet >/dev/null
info "Runtime SA: datastore.user (project)"

# ---- 4. Firestore and the report bucket ---------------------------------------
say "4. Firestore (default) database and report bucket"
# Only one database per project qualifies for the free quota, so the six builds
# share (default) and namespace collections by product (manifest_runs, ...).
if exists gcloud firestore databases describe --database="(default)" --project="$PROJECT"; then
  info "Firestore (default) exists — shared; MANIFEST uses manifest_* collections"
else
  run gcloud firestore databases create --project="$PROJECT" \
    --database="(default)" --location="$REGION" --type=firestore-native
fi

if exists gcloud storage buckets describe "$BUCKET" --project="$PROJECT"; then
  info "Bucket $BUCKET exists"
else
  # Standard class in us-central1 = Free Tier. Soft delete off: deleted report
  # versions would otherwise keep counting against the 5 GB-month allowance.
  run gcloud storage buckets create "$BUCKET" --project="$PROJECT" \
    --location="$REGION" --default-storage-class=STANDARD \
    --uniform-bucket-level-access --public-access-prevention \
    --soft-delete-duration=0
fi
run gcloud storage buckets update "$BUCKET" --update-labels="${LABEL_KEY}=${LABEL_VAL}" >/dev/null
run gcloud storage buckets add-iam-policy-binding "$BUCKET" \
  --member="serviceAccount:${RUNTIME_SA}" --role=roles/storage.objectAdmin >/dev/null
info "Runtime SA: storage.objectAdmin on $BUCKET only"

# ---- 5. Artifact Registry with keep-latest cleanup -----------------------------
say "5. Artifact Registry repo $AR_REPO"
if exists gcloud artifacts repositories describe "$AR_REPO" --project="$PROJECT" --location="$REGION"; then
  info "Repo $AR_REPO exists"
else
  run gcloud artifacts repositories create "$AR_REPO" --project="$PROJECT" \
    --location="$REGION" --repository-format=docker \
    --description="Tech-writing portfolio images (keep latest only)" \
    --labels="${LABEL_KEY}=tech-writing"
fi
POLICY_FILE="$(mktemp)"; trap 'rm -f "$POLICY_FILE"' EXIT
cat >"$POLICY_FILE" <<'JSON'
[
  { "name": "keep-latest",
    "action": { "type": "Keep" },
    "mostRecentVersions": { "keepCount": 1 } },
  { "name": "delete-everything-else",
    "action": { "type": "Delete" },
    "condition": { "tagState": "any" } }
]
JSON
run gcloud artifacts repositories set-cleanup-policies "$AR_REPO" --project="$PROJECT" \
  --location="$REGION" --policy="$POLICY_FILE" --no-dry-run >/dev/null
info "Cleanup: keep 1 most recent version per image (runs about daily)"
run gcloud artifacts repositories add-iam-policy-binding "$AR_REPO" --project="$PROJECT" \
  --location="$REGION" --member="serviceAccount:${DEPLOY_SA}" \
  --role=roles/artifactregistry.writer >/dev/null
info "Deploy SA: artifactregistry.writer on $AR_REPO only"

# ---- 6. Secret container (no value) -------------------------------------------
say "6. Secret $SECRET_ID"
if exists gcloud secrets describe "$SECRET_ID" --project="$PROJECT"; then
  info "Secret exists"
else
  run gcloud secrets create "$SECRET_ID" --project="$PROJECT" \
    --replication-policy=automatic --labels="${LABEL_KEY}=tech-writing"
fi
run gcloud secrets add-iam-policy-binding "$SECRET_ID" --project="$PROJECT" \
  --member="serviceAccount:${RUNTIME_SA}" --role=roles/secretmanager.secretAccessor >/dev/null
info "Runtime SA: secretAccessor on $SECRET_ID only"

# ---- 7. Budget ------------------------------------------------------------------
say "7. Budget $BUDGET_NAME"
if gcloud billing budgets list --billing-account="$BILLING_ACCOUNT" \
     --format='value(displayName)' 2>/dev/null | grep -qx "$BUDGET_NAME"; then
  info "Budget exists"
else
  # exclude-all-credits: credits are added back, so alerts fire on real usage
  # even while the Free Trial credit is paying for it.
  run gcloud billing budgets create --billing-account="$BILLING_ACCOUNT" \
    --display-name="$BUDGET_NAME" --budget-amount="$BUDGET_AMOUNT" \
    --calendar-period=month --filter-projects="projects/${PROJECT}" \
    --credit-types-treatment=exclude-all-credits \
    --threshold-rule=percent=0.20 \
    --threshold-rule=percent=0.50 \
    --threshold-rule=percent=1.00
fi

# ---- 8. BigQuery custom quota ---------------------------------------------------
say "8. BigQuery QueryUsagePerDay = ${BQ_DAILY_GIB} GiB"
# Cloud Quotas expresses this quota in its own unit; read it rather than assume.
BQ_UNIT="$(gcloud beta quotas info describe QueryUsagePerDay --service=bigquery.googleapis.com \
  --project="$PROJECT" --format='value(metricUnit)' 2>/dev/null || true)"
case "$BQ_UNIT" in
  *MiBy*|*MiB*) BQ_VALUE=$((BQ_DAILY_GIB * 1024)) ;;
  *GiBy*|*GiB*) BQ_VALUE=$BQ_DAILY_GIB ;;
  *TiBy*|*TiB*) echo "   Unit is TiB; the smallest step (1 TiB) is above the ceiling. Set it in the console."; BQ_VALUE="" ;;
  *) echo "   Could not read the unit (got '${BQ_UNIT}'). Set 'Query usage per day' to ${BQ_DAILY_GIB} GiB in the console:"
     echo "   https://console.cloud.google.com/iam-admin/quotas?project=${PROJECT}"; BQ_VALUE="" ;;
esac
if [[ -n "$BQ_VALUE" ]]; then
  info "Unit $BQ_UNIT -> preferred value $BQ_VALUE"
  run gcloud beta quotas preferences create --project="$PROJECT" \
    --service=bigquery.googleapis.com --quota-id=QueryUsagePerDay \
    --preferred-value="$BQ_VALUE" --preference-id=bq-query-usage-per-day-20pct \
    --allow-high-percentage-quota-decrease \
    --justification="Hold portfolio project to 20% of the BigQuery Free Tier" \
    || info "Preference may already exist: gcloud beta quotas preferences list --project=$PROJECT"
fi

# ---- 9. Output: GitHub repo variables ---------------------------------------
say "Done. Set these GitHub repo variables (no secrets; WIF needs no keys):"
WIF_PROVIDER="${POOL_NAME}/providers/${PROVIDER_ID}"
for entry in "${GITHUB_REPOS[@]}"; do
  repo="${GITHUB_OWNER}/${entry%%:*}"
  cat <<EOF
   gh variable set GCP_PROJECT      -R $repo -b "$PROJECT"
   gh variable set GCP_REGION       -R $repo -b "$REGION"
   gh variable set GCP_WIF_PROVIDER -R $repo -b "$WIF_PROVIDER"
   gh variable set GCP_DEPLOY_SA    -R $repo -b "$DEPLOY_SA"
   gh variable set GCP_RUNTIME_SA   -R $repo -b "$RUNTIME_SA"
   gh variable set GCP_BUCKET       -R $repo -b "$BUCKET"
   gh variable set GCP_AR_REPO      -R $repo -b "${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}"
EOF
done
cat <<EOF

   Add the Gemini key yourself (it is read from your keyboard, never stored on disk):
   read -rs KEY && printf %s "\$KEY" | gcloud secrets versions add $SECRET_ID --project=$PROJECT --data-file=- ; unset KEY

   WIF bindings can take up to 5 minutes to propagate before the first deploy.
EOF
