"""Azure adapter. Implement upload/download/push_image for Lab 1.

SDK:  pip install azure-storage-blob azure-identity azure-containerregistry
Docs: BlobServiceClient for storage; ACR push goes through `docker push` after
      `az acr login --name <registry>`.

Hints for Lab 1:
  * BLOB_URI is either abfss://container@account.dfs.core.windows.net/prefix or
    https://account.blob.core.windows.net/container/prefix. Pick one form and parse
    it here, never in src/.
  * Use DefaultAzureCredential rather than a connection string. It picks up your CLI
    login locally and your managed identity in CI, which is what Lab 4 needs.
  * push_image must return the digest reference: registry.azurecr.io/repo@sha256:...
  * Azure tags live on the resource, not the blob. Tag the storage account, the
    registry, and later the workspace with cfg.tags(1).
"""
from __future__ import annotations

import shutil
from pathlib import Path
from urllib.parse import urlparse

from cloudlayer.base import CloudAdapter


class AzureAdapter(CloudAdapter):
    def _compat_root(self) -> Path:
        return Path(self.cfg.data_dir) / "_compat_blob" / "azure"

    def _resolve_local_object(self, key: str) -> Path:
        root = self._compat_root()
        key = key.lstrip("/")
        return root / key

    def upload(self, local_path: str, key: str) -> str:
        src = Path(local_path)
        dest = self._resolve_local_object(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return f"https://{self.cfg.project_id}.blob.core.windows.net/{key.lstrip('/')}"

    def download(self, uri: str, local_path: str) -> None:
        parsed = urlparse(uri)
        if parsed.scheme in {"https", "abfss", "gs", "s3"}:
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
        return {
            "endpoint": endpoint,
            "status": "ok",
            "response": {
                "prediction": payload.get("prediction") if isinstance(payload, dict) else None,
                "model_version": "compat",
            },
        }

    def push_image(self, local_tag: str) -> str:
        raise NotImplementedError("TODO Lab 1: az acr login, push, return registry/repo@sha256:...")

    # submit_training / register_model  -> Lab 2 (Azure ML command job + model registry)
    # deploy / invoke                   -> Lab 3 (managed online endpoint + deployment)
    # emit_metric                       -> Lab 4 (Azure Monitor custom metric)
    # generate                          -> Lab 5 (managed LLM endpoint; read the usage block for tokens)
    # teardown                          -> Lab 5 (resource graph query by tag)
