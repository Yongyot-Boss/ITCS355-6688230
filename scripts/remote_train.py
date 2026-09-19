"""Download a GCS dataset, run training, and upload job artifacts."""
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from google.cloud import storage


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20260101)
    parser.add_argument("--n-estimators", type=int, default=200)
    parser.add_argument("--max-depth", type=int, default=8)
    parser.add_argument("--min-samples-leaf", type=int, default=5)
    parser.add_argument("--blob-uri", default=os.environ.get("BLOB_URI"))
    args = parser.parse_args()

    if not args.blob_uri:
        raise ValueError("BLOB_URI is required for managed training")
    os.environ["BLOB_URI"] = args.blob_uri
    base = urlparse(args.blob_uri)
    bucket = storage.Client().bucket(base.netloc)
    prefix = base.path.strip("/")
    raw_path = Path("data/raw/sensors.csv")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    bucket.blob(f"{prefix}/raw/sensors.csv").download_to_filename(raw_path)

    subprocess.run(
        [
            "python", "-m", "src.train", "--seed", str(args.seed),
            "--n-estimators", str(args.n_estimators), "--max-depth", str(args.max_depth),
            "--min-samples-leaf", str(args.min_samples_leaf),
            "--metrics-out", "reports/metrics.json",
        ],
        check=True,
    )
    subprocess.run(
        [
            "python", "scripts/export_model.py", "--seed", str(args.seed),
            "--out", "reports/model.joblib",
        ],
        check=True,
    )

    job_id = os.environ.get("AIP_JOB_NAME", "local-remote-training").rsplit("/", 1)[-1]
    for local_path in (Path("reports/metrics.json"), Path("reports/model.joblib"), Path("raw.dvc")):
        blob = bucket.blob(f"{prefix}/artifacts/{job_id}/{local_path.name}")
        blob.upload_from_filename(local_path)


if __name__ == "__main__":
    main()