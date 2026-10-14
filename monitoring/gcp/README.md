# GCP scheduled drift check

This deployment uses Cloud Scheduler to start a private Cloud Run Job every six hours.
The job reads its reference and current CSVs from GCS, emits PSI gauges to Cloud
Monitoring, and sends email through an authenticated SMTP relay when PSI reaches `0.25`.
All setup below uses `gcloud` and Docker.

## Prerequisites

- Google Cloud CLI (`gcloud`) and Docker installed and authenticated.
- A Google Cloud project with billing enabled.
- A private GCS bucket containing:
  - a fixed training/reference CSV, and
  - a production-input CSV refreshed before each scheduled run.
- Both CSVs contain the six feature columns in `src/data.py`.
- An SMTP relay that permits authenticated STARTTLS on port 587 and authorizes the
  configured sender address.
- Permission to enable APIs, create Artifact Registry repositories and service accounts,
  assign the documented IAM roles, and create Cloud Run Jobs and Cloud Scheduler jobs.

The deployment commands use environment variables so project-specific values do not need
to be committed. Set these values in your shell, replacing each example:

```bash
export PROJECT_ID="your-gcp-project-id"
export REGION="asia-southeast1"
export REPOSITORY="itcs355"
export DATA_BUCKET="your-private-data-bucket"
export REFERENCE_GCS_URI="gs://${DATA_BUCKET}/reference/sensors.csv"
export CURRENT_GCS_URI="gs://${DATA_BUCKET}/production/current.csv"
export TEST_ONLY="false"
export SMTP_HOST="smtp.gmail.com"
export SMTP_PORT="587"
export EMAIL_FROM="alerts@example.com"
export EMAIL_TO="on-call@example.com"
export SMTP_USERNAME_SECRET_ID="drift-smtp-username"
export SMTP_PASSWORD_SECRET_ID="drift-smtp-password"
```

The local, git-ignored `monitoring/gcp/deploy.env` file can hold deployment overrides.
It is loaded automatically by `deploy.sh`; `EMAIL_TO` is set there for this setup. You
can also change it in that file or override it in the shell after loading the file.
For an email-only test before production current data is available, set `TEST_ONLY=true`
and point `REFERENCE_GCS_URI` at an existing feature CSV. In this mode the job is
configured to compare a synthetic shifted copy of the reference to itself and no Scheduler
job is created. Do not leave test mode enabled for a production deployment.

Use an SMTP app password or relay credential, not a normal email account password. Do not
put credentials in these commands or in this repository. Create two permission-restricted
local files containing the SMTP username and password, then create Secret Manager secrets:

```bash
gcloud config set project "$PROJECT_ID"
gcloud secrets create "$SMTP_USERNAME_SECRET_ID" \
  --project="$PROJECT_ID" \
  --data-file=/secure/path/smtp-username.txt
gcloud secrets create "$SMTP_PASSWORD_SECRET_ID" \
  --project="$PROJECT_ID" \
  --data-file=/secure/path/smtp-password.txt
```

If a secret already exists, add a version instead:

```bash
gcloud secrets versions add "$SMTP_USERNAME_SECRET_ID" \
  --project="$PROJECT_ID" \
  --data-file=/secure/path/smtp-username.txt
gcloud secrets versions add "$SMTP_PASSWORD_SECRET_ID" \
  --project="$PROJECT_ID" \
  --data-file=/secure/path/smtp-password.txt
```

## Deploy

From the repository root, run:

```bash
bash monitoring/gcp/deploy.sh
```

The script enables required APIs, creates the Artifact Registry repository if needed,
builds and pushes the monitoring image, and pins the Cloud Run Job to the pushed image
digest. It then creates or updates the job and its six-hour Scheduler trigger. It also
creates separate service accounts and grants the job only bucket object-read, metric-write,
and SMTP-secret access; the Scheduler identity is granted permission to invoke this job.
The Cloud Monitoring dashboard is created or updated from
[`dashboard.json`](dashboard.json).

The script expects the GCS bucket and both CSV objects to exist, and the two secrets to
have at least one version. It does not create or populate the bucket. Review the script
before running it; it creates billable Google Cloud resources. Nothing is deployed merely
by building the local Docker image.

## Test and monitor

Run the production-input check once:

```bash
gcloud run jobs execute itcs355-drift-check \
  --project="$PROJECT_ID" --region="$REGION" --wait
```

If deployed with `TEST_ONLY=true`, execute the job once to send a synthetic drift alert.
This reads the reference CSV and shifts `temp_c` in memory; it does not change the GCS data:

```bash
gcloud run jobs execute itcs355-drift-check \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --wait
```

For production mode, Cloud Scheduler also supports **Run now** for a trigger test. Check the Cloud Run
Job execution logs and destination inbox to confirm completion and delivery. The Scheduler
request confirms that the job was started; it does not report the final execution result.
Cloud Monitoring records metrics named
`custom.googleapis.com/drift.psi.<feature>` in the **ITCS355 Feature drift** dashboard.

## Remove deployed resources

To stop future scheduled runs, delete the Scheduler job first, then the Cloud Run Job:

```bash
gcloud scheduler jobs delete itcs355-drift-check-schedule \
  --project="$PROJECT_ID" --location="$REGION"
gcloud run jobs delete itcs355-drift-check \
  --project="$PROJECT_ID" --region="$REGION"
```

The deployment-created service accounts, Artifact Registry repository, dashboard, and
secrets can be removed separately if they are not shared with other workloads. Inspect
their usage before deleting them. The GCS bucket and its data are not managed by this
deployment.
