"""Invoke a deployed inference endpoint with three known sensor payloads."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloudlayer.factory import get_adapter
from src import config

PAYLOADS = (
    {
        "temp_c": 78.4,
        "vibration_mm_s": 3.1,
        "pressure_kpa": 315.2,
        "hours_since_service": 4200.0,
        "load_pct": 68.0,
        "ambient_humidity": 55.0,
    },
    {
        "temp_c": 92.0,
        "vibration_mm_s": 4.8,
        "pressure_kpa": 290.0,
        "hours_since_service": 7200.0,
        "load_pct": 82.0,
        "ambient_humidity": 48.0,
    },
    {
        "temp_c": 61.0,
        "vibration_mm_s": 1.4,
        "pressure_kpa": 340.0,
        "hours_since_service": 900.0,
        "load_pct": 42.0,
        "ambient_humidity": 63.0,
    },
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    args = parser.parse_args()
    adapter = get_adapter(config.load())

    for index, payload in enumerate(PAYLOADS, start=1):
        result = adapter.invoke(args.endpoint, payload)
        probability = result.get("probability")
        if not isinstance(probability, (int, float)) or not 0 <= probability <= 1:
            raise RuntimeError(f"Smoke prediction {index} returned an invalid probability: {result}")
        if not result.get("model_version"):
            raise RuntimeError(f"Smoke prediction {index} did not include model_version: {result}")
        print(json.dumps({"case": index, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())