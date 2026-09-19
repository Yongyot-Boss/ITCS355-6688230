"""Register the selected Lab 2 run with complete model-version lineage.

    python scripts/register_model.py --run-id RUN_ID \
        --training-job-id JOB_ID --image-digest sha256:...
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mlflow

from cloudlayer.factory import get_adapter
from src import config
from src.train import dvc_hash, git_commit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--training-job-id", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--name", default=None)
    args = parser.parse_args()

    cfg = config.load(strict=False)
    mlflow.set_tracking_uri(cfg.mlflow_tracking_uri)
    run = mlflow.get_run(args.run_id)
    tags = {
        "git_commit": run.data.tags.get("git_commit", git_commit()),
        "data_version": run.data.tags.get("dvc_hash", dvc_hash()),
        "mlflow_run_id": args.run_id,
        "training_job_id": args.training_job_id,
        "image_digest": args.image_digest,
        "seed": str(run.data.params["seed"]),
        "metric_val": str(run.data.metrics["val_roc_auc"]),
        "metric_test": str(run.data.metrics["test_roc_auc"]),
    }
    model_uri = f"runs:/{args.run_id}/model"
    model_name = args.name or cfg.model_registry_name
    version = get_adapter(cfg).register_model(model_uri, model_name, tags)
    print(f"registered {model_uri} as {model_name} version {version}")
    for key, value in tags.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())