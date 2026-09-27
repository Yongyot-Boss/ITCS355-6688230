"""Measure labelled canary quality without consulting response model versions."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from sklearn.metrics import brier_score_loss, roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import data, seeds


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--rows", type=int, default=400)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--out", type=Path, default=Path("reports/lab3-canary-quality.json"))
    args = parser.parse_args()
    token = os.environ["VERTEX_TOKEN"]
    frame = data.load_raw(Path("data/raw/sensors.csv"))
    _, _, test = data.split(frame, seed=seeds.DEFAULT_SEED)
    sample = test.head(args.rows)
    labels = sample[data.TARGET].astype(int).tolist()
    bodies = sample[data.FEATURES].to_dict("records")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def predict(body: dict) -> float:
        response = requests.post(args.url, headers=headers, json=body, timeout=30)
        response.raise_for_status()
        # Deliberately ignore model_version: the detector observes outcomes only.
        return float(response.json()["probability"])

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        probabilities = list(pool.map(predict, bodies))
    elapsed = time.perf_counter() - started
    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "rows": len(labels),
        "duration_seconds": round(elapsed, 3),
        "roc_auc": round(float(roc_auc_score(labels, probabilities)), 6),
        "brier_score": round(float(brier_score_loss(labels, probabilities)), 6),
        "model_version_used": False,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
