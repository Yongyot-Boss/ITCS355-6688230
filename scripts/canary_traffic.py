"""Capture timestamped Vertex canary and rollback traffic evidence.

Run after both registered versions have been deployed to the same endpoint:

    python scripts/canary_traffic.py --endpoint ENDPOINT --split OLD_ID=90 NEW_ID=10
    # observe metrics without consulting the version labels
    python scripts/canary_traffic.py --endpoint ENDPOINT --split OLD_ID=100 NEW_ID=0

Each invocation appends before/after evidence to reports/lab3-canary.jsonl.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloudlayer.factory import get_adapter
from src import config


def parse_split(value: str) -> dict[str, int]:
    split: dict[str, int] = {}
    for item in value.split(","):
        model_id, separator, percent = item.partition("=")
        if not separator or not model_id or not percent:
            raise argparse.ArgumentTypeError("split must be DEPLOYED_MODEL_ID=PERCENT,...")
        try:
            split[model_id] = int(percent)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid percentage in {item!r}") from exc
    return split


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--split", required=True, type=parse_split)
    parser.add_argument("--evidence", default="reports/lab3-canary.jsonl")
    args = parser.parse_args()
    adapter = get_adapter(config.load())
    if not hasattr(adapter, "endpoint_traffic") or not hasattr(adapter, "set_traffic_split"):
        raise RuntimeError("canary_traffic.py currently supports the GCP Vertex adapter only")

    before = adapter.endpoint_traffic(args.endpoint)
    after = adapter.set_traffic_split(args.endpoint, args.split)
    evidence = {
        "timestamp": datetime.now(UTC).isoformat(),
        "endpoint": args.endpoint,
        "before": before,
        "requested": args.split,
        "after": after,
    }
    path = Path(args.evidence)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(evidence, sort_keys=True) + "\n")
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
