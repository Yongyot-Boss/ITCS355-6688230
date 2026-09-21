"""GCP adapter. Implement upload/download/push_image for Lab 1.

SDK:  pip install google-cloud-storage google-cloud-aiplatform
Docs: storage.Client for GCS; Artifact Registry push goes through `docker push` after
      `gcloud auth configure-docker <region>-docker.pkg.dev`.

Hints for Lab 1:
  * BLOB_URI looks like gs://bucket/prefix — parse it here, never in src/.
  * Artifact Registry paths are region-scoped:
        <region>-docker.pkg.dev/<project>/<repo>/<image>
    A common first failure is pushing to gcr.io out of habit; it is a different service.
  * push_image must return the digest reference, not the tag.
  * GCP calls them labels, not tags, and they must be lowercase with no spaces.
    cfg.tags(1) already satisfies that constraint — do not "improve" the values.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient

from cloudlayer.base import CloudAdapter


class GcpAdapter(CloudAdapter):
    def upload(self, local_path: str, key: str) -> str:
        from google.cloud import storage

        bucket_name, prefix = self._blob_location()
        blob_name = "/".join(part for part in (prefix, key.lstrip("/")) if part)
        blob = storage.Client(project=self.cfg.project_id).bucket(bucket_name).blob(blob_name)
        blob.upload_from_filename(local_path)
        return f"gs://{bucket_name}/{blob_name}"

    def download(self, uri: str, local_path: str) -> None:
        from google.cloud import storage

        parsed = urlparse(uri)
        if parsed.scheme != "gs" or not parsed.netloc or not parsed.path.strip("/"):
            raise ValueError(f"Expected a gs:// object URI, got {uri!r}")
        destination = Path(local_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        blob = storage.Client(project=self.cfg.project_id).bucket(parsed.netloc).blob(
            parsed.path.lstrip("/")
        )
        blob.download_to_filename(destination)

    def _blob_location(self) -> tuple[str, str]:
        parsed = urlparse(self.cfg.blob_uri)
        if parsed.scheme != "gs" or not parsed.netloc:
            raise ValueError(f"BLOB_URI must be a gs:// URI, got {self.cfg.blob_uri!r}")
        return parsed.netloc, parsed.path.strip("/")

    def push_image(self, local_tag: str) -> str:
        registry = self.cfg.container_registry.rstrip("/")
        if not registry:
            raise ValueError("CONTAINER_REGISTRY must be set for GCP image pushes")

        registry_host = f"{self.cfg.region}-docker.pkg.dev"
        if not registry.startswith(registry_host + "/"):
            raise ValueError(
                "CONTAINER_REGISTRY must start with "
                f"{registry_host}/ for the configured REGION"
            )

        image_name = local_tag.rsplit("/", 1)[-1]
        remote_tag = f"{registry}/{image_name}"

        subprocess.run(
            ["gcloud", "auth", "configure-docker", registry_host, "--quiet"],
            check=True,
        )
        subprocess.run(["docker", "tag", local_tag, remote_tag], check=True)
        subprocess.run(["docker", "push", remote_tag], check=True)

        inspection = subprocess.run(
            ["docker", "image", "inspect", remote_tag, "--format", "{{json .RepoDigests}}"],
            check=True,
            capture_output=True,
            text=True,
        )
        digests = json.loads(inspection.stdout)
        remote_repository = remote_tag.rsplit(":", 1)[0]
        for digest in digests:
            if digest.startswith(remote_repository + "@sha256:"):
                return digest
        raise RuntimeError(f"No pushed digest found for {remote_tag}")

    def submit_training(self, image_uri: str, args: dict[str, object]) -> str:
        job_id = str(args.get("job_id") or f"itcs355-{os.getpid()}-{os.urandom(3).hex()}")
        machine_type = str(args.get("instance", "e2-standard-4"))
        command = [str(value) for value in args.get("command", ["python", "scripts/remote_train.py"])]
        if "command" not in args:
            for name in ("seed", "n_estimators", "max_depth", "min_samples_leaf"):
                if name in args:
                    command.extend([f"--{name.replace('_', '-')}", str(args[name])])
        service_account = str(args.get("service_account") or self.cfg.identity_ref)
        blob_uri = str(args.get("blob_uri") or self.cfg.blob_uri)
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.iam\.gserviceaccount\.com", service_account):
            service_account = ""

        command_args = ["--project", self.cfg.project_id, "--region", self.cfg.region, "--display-name", job_id]
        command_args += [
            "--worker-pool-spec",
            f"machine-type={machine_type},replica-count=1,container-image-uri={image_uri}",
            "--command",
            command[0],
            "--args",
            ",".join([*command[1:], "--blob-uri", blob_uri]),
            "--format",
            "value(name)",
        ]
        if service_account:
            command_args[2:2] = ["--service-account", service_account]
        try:
            submitted = subprocess.run(
                ["gcloud", "ai", "custom-jobs", "create", *command_args],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "gcloud returned no diagnostic output").strip()
            raise RuntimeError(f"Vertex custom job submission failed: {detail}") from exc
        return submitted.stdout.strip() or job_id

    def wait_training(self, job_id: str) -> dict[str, object]:
        terminal_states = {
            "JOB_STATE_SUCCEEDED",
            "JOB_STATE_FAILED",
            "JOB_STATE_CANCELLED",
            "JOB_STATE_PAUSED",
            "JOB_STATE_EXPIRED",
        }
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            result = subprocess.run(
                [
                    "gcloud", "ai", "custom-jobs", "describe", job_id,
                    "--project", self.cfg.project_id, "--region", self.cfg.region,
                    "--format", "json",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            status = json.loads(result.stdout)
            state = status.get("state")
            if state in terminal_states:
                if state != "JOB_STATE_SUCCEEDED":
                    detail = status.get("error", status.get("stateMessage", "no error detail"))
                    raise RuntimeError(f"Vertex custom job {state}: {detail}")
                return status
            time.sleep(10)
        raise TimeoutError(f"Vertex custom job did not finish within 3600 seconds: {job_id}")

    def register_model(
        self,
        model_uri: str,
        name: str,
        lineage: dict[str, str] | None = None,
    ) -> str:
        """Register an MLflow model and put lineage on the registered version."""
        client = MlflowClient(tracking_uri=self.cfg.mlflow_tracking_uri)
        try:
            client.get_registered_model(name)
        except MlflowException:
            client.create_registered_model(name)

        run_id = model_uri.removeprefix("runs:/").split("/", 1)[0]
        source_uri = model_uri
        run = client.get_run(run_id) if run_id else None
        if run and getattr(run, "outputs", None) and run.outputs.model_outputs:
            logged_model = client.get_logged_model(run.outputs.model_outputs[-1].model_id)
            source_uri = logged_model.model_uri
        version = client.create_model_version(
            name=name,
            source=source_uri,
            run_id=run_id or None,
        )
        tags = dict(lineage or {})
        if run:
            tags.setdefault("git_commit", run.data.tags.get("git_commit", "unknown"))
            tags.setdefault("data_version", run.data.tags.get("dvc_hash", "unknown"))
            tags.setdefault("seed", str(run.data.params.get("seed", "unknown")))
            tags.setdefault("metric_val", str(run.data.metrics.get("val_roc_auc", "unknown")))
            tags.setdefault("metric_test", str(run.data.metrics.get("test_roc_auc", "unknown")))
        tags.setdefault("mlflow_run_id", run_id)
        required = (
            "git_commit", "data_version", "mlflow_run_id", "training_job_id",
            "image_digest", "seed", "metric_val", "metric_test",
        )
        missing = [key for key in required if not tags.get(key)]
        if missing:
            raise ValueError("Missing model lineage fields: " + ", ".join(missing))
        for key, value in tags.items():
            client.set_model_version_tag(name, version.version, key, str(value))
        return str(version.version)

    def promote_model(self, name: str, version: str, stage: str = "staging") -> None:
        client = MlflowClient(tracking_uri=self.cfg.mlflow_tracking_uri)
        client.set_registered_model_alias(name, stage, version)

    # submit_training / register_model  -> Lab 2 (Vertex custom training + Model Registry)
    # deploy / invoke                   -> Lab 3 (Vertex Endpoint)
    # emit_metric                       -> Lab 4 (Cloud Monitoring time series)
    # generate                          -> Lab 5 (managed LLM endpoint; read usageMetadata for tokens)
    # teardown                          -> Lab 5 (filter resources by label)
