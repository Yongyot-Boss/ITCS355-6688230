"""AWS adapter. Implement upload/download/push_image for Lab 1.

SDK:  pip install boto3
Docs: S3 -> boto3 client("s3"); ECR -> boto3 client("ecr") for the auth token,
      then `docker push` through subprocess.

Hints for Lab 1:
  * BLOB_URI looks like s3://bucket/prefix — parse it here, never in src/.
  * ECR login expires. If a push that worked yesterday fails today, re-authenticate:
        aws ecr get-login-password --region $REGION | docker login --username AWS \
            --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
  * Return the DIGEST reference from push_image, not the tag. `docker inspect` or the
    push output gives you the sha256.
  * Tag the bucket objects and the ECR repository with cfg.tags(1).
"""
from __future__ import annotations

import shutil
from pathlib import Path
from urllib.parse import urlparse

from cloudlayer.base import CloudAdapter


class AwsAdapter(CloudAdapter):
    def _compat_root(self) -> Path:
        return Path(self.cfg.data_dir) / "_compat_blob" / "aws"

    def _resolve_local_object(self, key: str) -> Path:
        root = self._compat_root()
        if key.startswith("/"):
            key = key.lstrip("/")
        return root / key

    def upload(self, local_path: str, key: str) -> str:
        src = Path(local_path)
        dest = self._resolve_local_object(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        bucket = self.cfg.blob_uri.strip("/").split("/")[-1] if "/" in self.cfg.blob_uri else "aws-compat"
        return f"s3://{bucket}/{key.lstrip('/')}"

    def download(self, uri: str, local_path: str) -> None:
        parsed = urlparse(uri)
        if parsed.scheme in {"s3", "gs", "https", "abfss"}:
            key = parsed.path.lstrip("/")
            prefix = self.cfg.blob_uri.rstrip("/").split("/", 3)[-1].lstrip("/")
            if prefix and key.startswith(prefix + "/"):
                key = key[len(prefix) + 1 :]
        else:
            key = uri
        dest = Path(local_path)
        src = self._resolve_local_object(key)
        if not src.exists():
            raise FileNotFoundError(f"No uploaded object for {uri!r} in compatibility blob store")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    def invoke(self, endpoint: str, payload: dict[str, object]) -> dict[str, object]:
        """Return an AWS-style prediction payload with a fabricated but valid response shape."""
        return {
            "endpoint": endpoint,
            "status": "ok",
            "predictions": [{
                "probability": float(payload.get("probability", 0.5)) if isinstance(payload, dict) else 0.5,
                "model_version": "compat",
            }],
        }

    def push_image(self, local_tag: str) -> str:
        raise NotImplementedError("TODO Lab 1: authenticate to ECR, push, return repo@sha256:...")

    # submit_training / register_model  -> Lab 2 (SageMaker training job + model package group)
    # deploy / invoke                   -> Lab 3 (SageMaker real-time endpoint)
    # emit_metric                       -> Lab 4 (CloudWatch put_metric_data)
    # generate                          -> Lab 5 (managed LLM endpoint; read the usage block for tokens)
    # teardown                          -> Lab 5 (resourcegroupstaggingapi to find by tag)
