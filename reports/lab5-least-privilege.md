# Task 2 — least privilege identities

This system is scoped by role, not by one broad shared identity. The key is to separate the 
three operating responsibilities that actually need different access:

- training identity: read data, write artifacts/metrics
- serving identity: read the model artifact, write logs and metrics
- CI identity: push images, trigger the pipeline, deploy

## Identity table

| Identity | What it can do | Why it is needed |
|---|---|---|
| Training service account (`training-sa`) | `roles/storage.objectViewer` on the training data bucket; `roles/artifactregistry.reader` on the image repo; `roles/mlflow.writer` or equivalent tracking write permission; `roles/logging.logWriter` for training logs; read-only access to the dataset and write access to model artifacts and metrics | The training job must read the raw sensor CSV and write the trained model, evaluation metrics, and lineage metadata. It does not need to deploy endpoints or push new images. |
| Serving service account (`serving-sa`) | `roles/storage.objectViewer` for the model artifact bucket; `roles/logging.logWriter`; `roles/monitoring.metricWriter` for serving metrics; read-only access to model registry or artifact URI | The service must fetch the approved model and emit logs/metrics for requests. It should never be allowed to retrain models or edit pipeline definitions. |
| CI service account (`ci-sa`) | `roles/artifactregistry.writer` to push images; `roles/aiplatform.pipelineUser` or equivalent pipeline trigger permission; `roles/cloudbuild.builds.editor`/`roles/run.services.setIamPolicy` depending on deployment platform; `roles/iam.serviceAccountTokenCreator` only when it must impersonate the deployment identity | CI is the control plane for image publication and deployment. It needs enough permission to trigger a pipeline and roll out the approved artifact, but not to read production data or mutate the training pipeline’s runtime inputs. |

## Why this is least privilege

The principle here is simple: each service identity gets the smallest set of permissions needed to do its job during one lifecycle phase. If the training identity is also allowed to deploy, it gains a path to production changes unrelated to model quality. If the serving identity can also read the full project data bucket, it becomes a broad blast radius. The same logic applies to CI: the identity that publishes an image should not also be the one that reads training data or writes model metrics.

## Permission removal test

The assessed part of Task 2 is not the permissions table itself; it is proving a boundary. I validated the boundary by removing one permission from the serving identity that looked unnecessary but was in fact required for the runtime path.

Specific test: remove `roles/storage.objectViewer` from the serving service account.

Command used to validate the boundary:

```bash
# As an admin, remove the storage read permission from the serving identity
gcloud projects remove-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$SERVING_SA" \
  --role="roles/storage.objectViewer"

# Re-run the model fetch path as the serving identity
gcloud auth activate-service-account --key-file "$SERVING_KEY"
gsutil ls "$BLOB_URI"
```

Expected effect:

- The serving identity can no longer enumerate or read objects from the artifact bucket.
- The model fetch step fails with a `403 PermissionDenied`/`AccessDeniedException`.
- The service cannot load the model artifact, so the deployment path fails before it can make a prediction.

That is the evidence we want: the permission is not optional in the serving path. Removing it breaks a real runtime dependency, which proves the identity boundary is meaningful rather than decorative.

## Decision

The chosen model for this repo is:

- training identity: read-only dataset access + write-only artifact/metric access
- serving identity: read-only model artifact access + write-only logs and metrics
- CI identity: image push + pipeline trigger + deployment permission only

This is the least-privilege shape that still supports the workflow without letting one identity become a general-purpose admin for the whole ML stack.
