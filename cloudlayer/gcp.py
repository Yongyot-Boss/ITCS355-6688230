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
import base64
import hashlib
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

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

    def emit_metric(self, name: str, value: float, unit: str = "1") -> None:
        """Write a gauge sample to Cloud Monitoring as a custom metric."""
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/monitoring.write"]
        )
        session = AuthorizedSession(credentials)
        response = session.post(
            f"https://monitoring.googleapis.com/v3/projects/{self.cfg.project_id}/timeSeries",
            json={
                "timeSeries": [
                    {
                        "metric": {"type": f"custom.googleapis.com/{name}"},
                        "resource": {
                            "type": "global",
                            "labels": {"project_id": self.cfg.project_id},
                        },
                        "metricKind": "GAUGE",
                        "valueType": "DOUBLE",
                        "unit": unit,
                        "points": [
                            {
                                "interval": {
                                    "endTime": time.strftime(
                                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                                    )
                                },
                                "value": {"doubleValue": value},
                            }
                        ],
                    }
                ]
            },
            timeout=20,
        )
        if not response.ok:
            raise RuntimeError(
                f"Cloud Monitoring rejected metric {name!r}: "
                f"HTTP {response.status_code}: {response.text}"
            )

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
        labels = args.get("labels") or {"course": "itcs355", "student": self.cfg.project_id, "lab": "2"}
        command_args[2:2] = ["--labels", ",".join(f"{key}={value}" for key, value in labels.items())]
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

    def _vertex_session(self):
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        return AuthorizedSession(credentials)

    def _vertex_request(self, method: str, path: str, **kwargs):
        url = f"https://{self.cfg.region}-aiplatform.googleapis.com/v1/{path.lstrip('/')}"
        response = self._vertex_session().request(method, url, timeout=60, **kwargs)
        if not response.ok:
            raise RuntimeError(
                f"Vertex AI {method} {path} failed ({response.status_code}): {response.text}"
            )
        return response.json() if response.content else {}

    def _wait_vertex_operation(self, operation: dict[str, object]) -> dict[str, object]:
        operation_name = operation.get("name")
        if not isinstance(operation_name, str):
            raise RuntimeError(f"Vertex AI returned an invalid operation: {operation}")

        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            status = self._vertex_request("GET", operation_name)
            if status.get("done"):
                error = status.get("error")
                if error:
                    raise RuntimeError(f"Vertex AI operation failed: {error}")
                response = status.get("response", {})
                return response if isinstance(response, dict) else {}
            time.sleep(5)
        raise TimeoutError(f"Vertex AI operation did not finish within 3600 seconds: {operation_name}")

    def _registered_model_source(self, model_ref: str) -> tuple[str, str]:
        import mlflow
        from mlflow.tracking import MlflowClient

        model_uri = model_ref if model_ref.startswith("models:/") else f"models:/{model_ref}"
        model_name, separator, model_version = model_uri.removeprefix("models:/").rpartition("/")
        if not separator or not model_name or not model_version:
            raise ValueError("MODEL_REF must identify a registered model version, such as models:/name/3")

        mlflow.set_tracking_uri(self.cfg.mlflow_tracking_uri)
        client = MlflowClient(tracking_uri=self.cfg.mlflow_tracking_uri)
        registered_version = client.get_model_version(model_name, model_version)
        source_uri = registered_version.source
        if registered_version.run_id:
            run = client.get_run(registered_version.run_id)
            model_outputs = getattr(getattr(run, "outputs", None), "model_outputs", ())
            if model_outputs:
                source_uri = client.get_logged_model(model_outputs[-1].model_id).model_uri

        return source_uri, model_version

    def _stage_model_artifact(self, model_ref: str, endpoint: str) -> tuple[str, str]:
        import joblib
        import mlflow.sklearn
        from google.cloud import storage

        source_uri, model_version = self._registered_model_source(model_ref)
        model = mlflow.sklearn.load_model(source_uri)
        bucket_name, prefix = self._blob_location()
        object_name = "/".join(
            part for part in (prefix, "serving", endpoint, model_version, "model.joblib") if part
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            model_file = Path(temporary_directory) / "model.joblib"
            joblib.dump(model, model_file)
            storage.Client(project=self.cfg.project_id).bucket(bucket_name).blob(
                object_name
            ).upload_from_filename(str(model_file))
        return f"gs://{bucket_name}/{object_name}", model_version

    def deploy(self, model_ref: str, endpoint: str, instance: str) -> str:
        """Upload a registered sklearn version and deploy its serving image to Vertex AI."""
        image_uri = os.environ.get("SERVING_IMAGE_URI", "").strip()
        if not image_uri or "@sha256:" not in image_uri:
            raise ValueError(
                "SERVING_IMAGE_URI must be set to a digest-pinned serving image; run make deploy"
            )
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", endpoint):
            raise ValueError("endpoint must be a lowercase Vertex AI resource ID")
        if not instance:
            raise ValueError("instance must be a Vertex AI machine type")

        artifact_uri, model_version = self._stage_model_artifact(model_ref, endpoint)
        parent = f"projects/{self.cfg.project_id}/locations/{self.cfg.region}"
        labels = self.cfg.tags(3)
        # Vertex model IDs are immutable. A fresh ID lets a retry deploy a corrected
        # container while the old, labelled model remains available for rollback.
        model_id = (
            f"itcs355-"
            f"{hashlib.sha256(f'{endpoint}:{model_version}:{image_uri}'.encode()).hexdigest()[:24]}"
            f"-{int(time.time())}"
        )
        model_path = f"{parent}/models/{model_id}"
        try:
            upload = self._vertex_request(
                "POST",
                f"{parent}/models:upload",
                params={"modelId": model_id},
                json={
                    "model": {
                        "displayName": f"{endpoint}-{model_version}",
                        "artifactUri": artifact_uri.rsplit("/", 1)[0],
                        "containerSpec": {
                            "imageUri": image_uri,
                            "healthRoute": "/ready",
                            "predictRoute": "/predict",
                            "ports": [{"containerPort": 8080}],
                            "env": [
                                {"name": "MODEL_ARTIFACT_URI", "value": artifact_uri},
                                {"name": "MODEL_VERSION", "value": model_version},
                            ],
                        },
                        "labels": labels,
                    }
                },
            )
            uploaded_model = self._wait_vertex_operation(upload)
            model_name = uploaded_model.get("model") or uploaded_model.get("name")
        except RuntimeError as exc:
            if "(409)" not in str(exc):
                raise
            model_name = self._vertex_request("GET", model_path).get("name")
        if not isinstance(model_name, str):
            raise RuntimeError(f"Vertex AI model upload returned no model resource: {model_name}")

        endpoint_path = f"{parent}/endpoints/{endpoint}"
        try:
            create_endpoint = self._vertex_request(
                "POST",
                f"{parent}/endpoints",
                params={"endpointId": endpoint},
                json={"displayName": endpoint, "labels": labels},
            )
            endpoint_resource = self._wait_vertex_operation(create_endpoint)
            endpoint_name = endpoint_resource.get("name", endpoint_path)
            if not isinstance(endpoint_name, str):
                endpoint_name = endpoint_path
        except RuntimeError as exc:
            if "(409)" not in str(exc):
                raise
            endpoint_resource = self._vertex_request("GET", endpoint_path)
            endpoint_name = endpoint_resource.get("name", endpoint_path)
        if not isinstance(endpoint_name, str):
            endpoint_name = endpoint_path
        if any(
            deployed_model.get("model") == model_name
            for deployed_model in endpoint_resource.get("deployedModels", [])
        ):
            return endpoint_name
        deployed_model_id = f"itcs355-{endpoint}-{model_version}"
        # A first deployment owns all traffic.  For a canary, set
        # INITIAL_TRAFFIC_PERCENT=10 before deploying the second version.  Vertex's
        # special key "0" means the model in this deploy request; existing deployed
        # model IDs retain the remaining traffic.
        try:
            new_traffic = int(os.environ.get("INITIAL_TRAFFIC_PERCENT", "100"))
        except ValueError as exc:
            raise ValueError("INITIAL_TRAFFIC_PERCENT must be an integer from 0 to 100") from exc
        if not 0 <= new_traffic <= 100:
            raise ValueError("INITIAL_TRAFFIC_PERCENT must be from 0 to 100")
        existing_split = endpoint_resource.get("trafficSplit", {})
        if not isinstance(existing_split, dict):
            existing_split = {}
        if existing_split and new_traffic < 100:
            old_total = sum(int(weight) for weight in existing_split.values())
            if old_total <= 0:
                raise RuntimeError("existing endpoint has no routable traffic for a canary")
            remaining = 100 - new_traffic
            old_items = list(existing_split.items())
            traffic_split = {
                str(model_id): remaining * int(weight) // old_total
                for model_id, weight in old_items
            }
            # Integer rounding must still leave the exact remainder Vertex requires.
            traffic_split[str(old_items[0][0])] += remaining - sum(traffic_split.values())
            traffic_split["0"] = new_traffic
        else:
            traffic_split = {"0": 100}

        deployment = self._vertex_request(
            "POST",
            f"{endpoint_name}:deployModel",
            json={
                "deployedModel": {
                    "model": model_name,
                    "displayName": deployed_model_id,
                    "dedicatedResources": {
                        "machineSpec": {"machineType": instance},
                        "minReplicaCount": 1,
                        "maxReplicaCount": 1,
                    },
                },
                "trafficSplit": traffic_split,
            },
        )
        self._wait_vertex_operation(deployment)
        return str(endpoint_name)

    def endpoint_traffic(self, endpoint: str) -> dict[str, int]:
        """Return the current Vertex traffic split, keyed by deployed-model ID."""
        endpoint_name = self._endpoint_name(endpoint)
        resource = self._vertex_request("GET", endpoint_name)
        split = resource.get("trafficSplit", {})
        if not isinstance(split, dict):
            raise RuntimeError(f"Vertex endpoint returned an invalid traffic split: {split!r}")
        return {str(model_id): int(percent) for model_id, percent in split.items()}

    def set_traffic_split(self, endpoint: str, split: dict[str, int]) -> dict[str, int]:
        """Atomically update a Vertex endpoint's traffic split and wait for completion."""
        if not split or sum(split.values()) != 100 or any(not 0 <= value <= 100 for value in split.values()):
            raise ValueError("traffic split must contain non-negative integer percentages totalling 100")
        endpoint_name = self._endpoint_name(endpoint)
        resource = self._vertex_request("GET", endpoint_name)
        deployed_ids = {
            str(deployed["id"])
            for deployed in resource.get("deployedModels", [])
            if isinstance(deployed, dict) and deployed.get("id") is not None
        }
        unknown = set(split) - deployed_ids
        if unknown:
            raise ValueError(f"traffic split contains unknown deployed model IDs: {sorted(unknown)}")
        operation = self._vertex_request(
            "PATCH",
            endpoint_name,
            params={"updateMask": "traffic_split"},
            json={"trafficSplit": split},
        )
        self._wait_vertex_operation(operation)
        return self.endpoint_traffic(endpoint_name)

    def _endpoint_name(self, endpoint: str) -> str:
        endpoint_name = endpoint.strip("/")
        if not endpoint_name.startswith("projects/"):
            endpoint_name = (
                f"projects/{self.cfg.project_id}/locations/{self.cfg.region}/endpoints/{endpoint_name}"
            )
        if not re.fullmatch(
            r"projects/[^/]+/locations/[^/]+/endpoints/[a-z][a-z0-9-]{0,62}",
            endpoint_name,
        ):
            raise ValueError("endpoint must be a Vertex endpoint ID or full endpoint resource name")
        return endpoint_name

    def invoke(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Invoke the configured /predict route and return the service JSON response."""
        endpoint_name = self._endpoint_name(endpoint)

        result = self._vertex_request(
            "POST",
            f"{endpoint_name}:rawPredict",
            # rawPredict forwards this JSON body unchanged to the configured
            # container predictRoute.  The HttpBody wrapper belongs to generated
            # client stubs, not this REST endpoint.
            json=payload,
        )
        output = result.get("output")
        # The REST endpoint normally returns the container JSON directly.  Some
        # generated client transports instead expose its bytes under ``output``.
        if output is None:
            return result
        if not isinstance(output, str):
            raise RuntimeError(f"Vertex AI raw prediction returned invalid response body: {result}")
        try:
            return json.loads(base64.b64decode(output, validate=True))
        except (ValueError, json.JSONDecodeError):
            return json.loads(output)

    def teardown(self, tags: dict[str, str]) -> list[str]:
        """Delete tagged Vertex endpoints/models and cancel matching active jobs."""
        listed = subprocess.run(
            [
                "gcloud", "ai", "custom-jobs", "list",
                "--project", self.cfg.project_id,
                "--region", self.cfg.region,
                "--format", "json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        jobs = json.loads(listed.stdout)
        terminal_states = {
            "JOB_STATE_SUCCEEDED",
            "JOB_STATE_FAILED",
            "JOB_STATE_CANCELLED",
            "JOB_STATE_PAUSED",
            "JOB_STATE_EXPIRED",
        }
        affected: list[str] = []
        for job in jobs:
            labels = job.get("labels", {})
            if any(labels.get(key) != value for key, value in tags.items()):
                continue
            if job.get("state") in terminal_states:
                continue
            name = job["name"]
            subprocess.run(
                [
                    "gcloud", "ai", "custom-jobs", "cancel", name,
                    "--project", self.cfg.project_id,
                    "--region", self.cfg.region,
                    "--quiet",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            affected.append(f"cancel requested: {name}")

        parent = f"projects/{self.cfg.project_id}/locations/{self.cfg.region}"
        # Vertex refuses to delete an endpoint while it has deployed models.
        # Undeploy those replicas first (including zero-traffic replicas, which
        # are still billable), wait for each long-running operation, then delete
        # the endpoint before deleting its now-unreferenced Model resources.
        for collection in ("endpoints", "models"):
            page_token = None
            while True:
                params = {"pageSize": 100}
                if page_token:
                    params["pageToken"] = page_token
                response = self._vertex_request(
                    "GET", f"{parent}/{collection}", params=params
                )
                for resource in response.get(collection, []):
                    labels = resource.get("labels", {})
                    if any(labels.get(key) != value for key, value in tags.items()):
                        continue
                    resource_name = resource["name"]
                    if collection == "endpoints":
                        deployed_ids = [
                            str(deployed["id"])
                            for deployed in resource.get("deployedModels", [])
                            if deployed.get("id") is not None
                        ]
                        for index, deployed_id in enumerate(deployed_ids):
                            remaining_ids = deployed_ids[index + 1 :]
                            # The API applies this replacement split as part of
                            # the undeploy operation.  It therefore never leaves
                            # a live endpoint pointing at the retiring replica.
                            replacement_split = (
                                {
                                    candidate_id: 100 if offset == 0 else 0
                                    for offset, candidate_id in enumerate(remaining_ids)
                                }
                                if remaining_ids
                                else {}
                            )
                            self._wait_vertex_operation(
                                self._vertex_request(
                                    "POST",
                                    f"{resource_name}:undeployModel",
                                    json={
                                        "deployedModelId": deployed_id,
                                        "trafficSplit": replacement_split,
                                    },
                                )
                            )
                            affected.append(
                                f"undeploy requested: {resource_name}/{deployed_id}"
                            )
                    self._wait_vertex_operation(
                        self._vertex_request("DELETE", resource_name)
                    )
                    affected.append(f"delete requested: {resource_name}")
                page_token = response.get("nextPageToken")
                if not page_token:
                    break
        return affected

    # submit_training / register_model  -> Lab 2 (Vertex custom training + Model Registry)
    # deploy / invoke                   -> Lab 3 (Vertex Endpoint)
    # emit_metric                       -> Lab 4 (Cloud Monitoring time series)
    # generate                          -> Lab 5 (managed LLM endpoint; read usageMetadata for tokens)
    # teardown                          -> Lab 5 (filter resources by label)
