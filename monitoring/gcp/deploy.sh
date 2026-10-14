#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${script_dir}/deploy.env" ]]; then
  # shellcheck disable=SC1091
  source "${script_dir}/deploy.env"
fi

: "${PROJECT_ID:?Set PROJECT_ID}"
: "${REGION:?Set REGION}"
: "${REPOSITORY:?Set REPOSITORY}"
: "${DATA_BUCKET:?Set DATA_BUCKET}"
: "${REFERENCE_GCS_URI:?Set REFERENCE_GCS_URI}"
: "${SMTP_HOST:?Set SMTP_HOST}"
: "${SMTP_PORT:?Set SMTP_PORT}"
: "${EMAIL_FROM:?Set EMAIL_FROM}"
: "${EMAIL_TO:?Set EMAIL_TO}"
: "${SMTP_USERNAME_SECRET_ID:?Set SMTP_USERNAME_SECRET_ID}"
: "${SMTP_PASSWORD_SECRET_ID:?Set SMTP_PASSWORD_SECRET_ID}"

TEST_ONLY="${TEST_ONLY:-false}"
if [[ "$TEST_ONLY" != "true" && "$TEST_ONLY" != "false" ]]; then
  echo "TEST_ONLY must be either true or false." >&2
  exit 2
fi

if [[ "$REFERENCE_GCS_URI" != "gs://${DATA_BUCKET}/"* ]]; then
  echo "REFERENCE_GCS_URI must belong to DATA_BUCKET." >&2
  exit 2
fi

if [[ "$TEST_ONLY" == "false" ]]; then
  : "${CURRENT_GCS_URI:?Set CURRENT_GCS_URI for scheduled production checks}"
  if [[ "$CURRENT_GCS_URI" != "gs://${DATA_BUCKET}/"* ]]; then
    echo "CURRENT_GCS_URI must belong to DATA_BUCKET." >&2
    exit 2
  fi
fi

JOB_NAME="${JOB_NAME:-itcs355-drift-check}"
SCHEDULER_JOB_NAME="${SCHEDULER_JOB_NAME:-${JOB_NAME}-schedule}"
SCHEDULE="${SCHEDULE:-0 */6 * * *}"
TIME_ZONE="${TIME_ZONE:-Etc/UTC}"
IMAGE_NAME="drift-check"
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short=12 HEAD)-$(date -u +%Y%m%d%H%M%S)}"
REGISTRY_HOST="${REGION}-docker.pkg.dev"
IMAGE_TAG_URI="${REGISTRY_HOST}/${PROJECT_ID}/${REPOSITORY}/${IMAGE_NAME}:${IMAGE_TAG}"
JOB_SERVICE_ACCOUNT="drift-job@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULER_SERVICE_ACCOUNT="drift-scheduler@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud config set project "$PROJECT_ID" --quiet
gcloud services enable \
  artifactregistry.googleapis.com \
  run.googleapis.com \
  cloudscheduler.googleapis.com \
  monitoring.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  --project="$PROJECT_ID"

if ! gcloud artifacts repositories describe "$REPOSITORY" \
  --project="$PROJECT_ID" --location="$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$REPOSITORY" \
    --project="$PROJECT_ID" \
    --location="$REGION" \
    --repository-format=docker \
    --description="ITCS355 drift monitoring images"
fi

gcloud auth configure-docker "$REGISTRY_HOST" --quiet
docker build -f monitoring/Dockerfile -t "$IMAGE_TAG_URI" .
docker push "$IMAGE_TAG_URI"
IMAGE_DIGEST_URI="$(
  docker image inspect "$IMAGE_TAG_URI" --format '{{index .RepoDigests 0}}'
)"
if [[ "$IMAGE_DIGEST_URI" != *@sha256:* ]]; then
  echo "Could not determine the pushed image digest for $IMAGE_TAG_URI." >&2
  exit 1
fi
printf 'Deploying image: %s\n' "$IMAGE_DIGEST_URI"

if ! gcloud iam service-accounts describe "$JOB_SERVICE_ACCOUNT" \
  --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create drift-job \
    --project="$PROJECT_ID" \
    --display-name="Scheduled model drift check"
fi
gcloud storage buckets add-iam-policy-binding "gs://${DATA_BUCKET}" \
  --project="$PROJECT_ID" \
  --member="serviceAccount:${JOB_SERVICE_ACCOUNT}" \
  --role="roles/storage.objectViewer"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${JOB_SERVICE_ACCOUNT}" \
  --role="roles/monitoring.metricWriter" \
  --quiet

for secret_id in "$SMTP_USERNAME_SECRET_ID" "$SMTP_PASSWORD_SECRET_ID"; do
  gcloud secrets describe "$secret_id" --project="$PROJECT_ID" >/dev/null
  gcloud secrets add-iam-policy-binding "$secret_id" \
    --project="$PROJECT_ID" \
    --member="serviceAccount:${JOB_SERVICE_ACCOUNT}" \
    --role="roles/secretmanager.secretAccessor"
done

if [[ "$TEST_ONLY" == "true" ]]; then
  job_args="-m,monitoring.drift_alert,--reference-gcs-uri,${REFERENCE_GCS_URI},--threshold,0.25,--emit,--test-synthetic-drift,--out,/tmp/drift_alert.json"
else
  job_args="-m,monitoring.drift_alert,--reference-gcs-uri,${REFERENCE_GCS_URI},--current-gcs-uri,${CURRENT_GCS_URI},--threshold,0.25,--emit,--out,/tmp/drift_alert.json"
fi

gcloud run jobs deploy "$JOB_NAME" \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --image="$IMAGE_DIGEST_URI" \
  --service-account="$JOB_SERVICE_ACCOUNT" \
  --command=python \
  --args="$job_args" \
  --set-env-vars="CLOUD_PROVIDER=gcp,PROJECT_ID=${PROJECT_ID},REGION=${REGION},SMTP_HOST=${SMTP_HOST},SMTP_PORT=${SMTP_PORT},DRIFT_EMAIL_FROM=${EMAIL_FROM},DRIFT_EMAIL_TO=${EMAIL_TO}" \
  --set-secrets="SMTP_USERNAME=${SMTP_USERNAME_SECRET_ID}:latest,SMTP_PASSWORD=${SMTP_PASSWORD_SECRET_ID}:latest" \
  --tasks=1 \
  --max-retries=0 \
  --task-timeout=10m

if [[ "$TEST_ONLY" == "false" ]]; then
  if ! gcloud iam service-accounts describe "$SCHEDULER_SERVICE_ACCOUNT" \
    --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud iam service-accounts create drift-scheduler \
      --project="$PROJECT_ID" \
      --display-name="Cloud Scheduler identity for drift job"
  fi
  gcloud run jobs add-iam-policy-binding "$JOB_NAME" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --member="serviceAccount:${SCHEDULER_SERVICE_ACCOUNT}" \
    --role="roles/run.invoker"

  SCHEDULER_URI="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_NAME}:run"
  if gcloud scheduler jobs describe "$SCHEDULER_JOB_NAME" \
    --project="$PROJECT_ID" --location="$REGION" >/dev/null 2>&1; then
    scheduler_action=update
  else
    scheduler_action=create
  fi
  gcloud scheduler jobs "$scheduler_action" http "$SCHEDULER_JOB_NAME" \
    --project="$PROJECT_ID" \
    --location="$REGION" \
    --schedule="$SCHEDULE" \
    --time-zone="$TIME_ZONE" \
    --uri="$SCHEDULER_URI" \
    --http-method=POST \
    --message-body='{}' \
    --oauth-service-account-email="$SCHEDULER_SERVICE_ACCOUNT" \
    --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform"
fi

if [[ "$TEST_ONLY" == "false" ]]; then
  dashboard_id="$(
      gcloud monitoring dashboards list \
        --project="$PROJECT_ID" \
        --filter='displayName="ITCS355 Feature drift"' \
        --format='value(name)' | head -n 1
  )"
  if [[ -n "$dashboard_id" ]]; then
      gcloud monitoring dashboards update "$dashboard_id" \
        --project="$PROJECT_ID" \
        --config-from-file="${script_dir}/dashboard.json"
  else
      gcloud monitoring dashboards create \
        --project="$PROJECT_ID" \
        --config-from-file="${script_dir}/dashboard.json"
  fi
fi

printf '\nDeployment complete.\n'
if [[ "$TEST_ONLY" == "true" ]]; then
  printf 'Run the synthetic email test with: gcloud run jobs execute %s --project=%s --region=%s --wait\n' \
    "$JOB_NAME" "$PROJECT_ID" "$REGION"
else
  printf 'Run once with: gcloud run jobs execute %s --project=%s --region=%s --wait\n' \
    "$JOB_NAME" "$PROJECT_ID" "$REGION"
fi
