"""Scheduled drift alerting for Lab 5.

This script keeps the monitoring loop real without requiring a provider-specific SDK:
- it compares the current production snapshot against the training reference window,
- writes the score to a local metrics file so dashboards can read it,
- sends a Line Notify alert when a token is configured.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib import request
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from monitoring.drift import compare
from src import data


def _send_line_notify(message: str, token: str | None) -> bool:
    if not token:
        print("LINE_NOTIFY_TOKEN not set — alert skipped.")
        return False
    payload = "message=" + message.replace("\n", " ")
    req = request.Request(
        "https://notify-api.line.me/api/notify",
        data=payload.encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", "replace")
        print(f"Line Notify response: {body}")
        return True
    except URLError as exc:
        print(f"Line Notify failed: {exc}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", type=Path, default=Path("data/raw/sensors.csv"))
    ap.add_argument("--current", type=Path, required=True)
    ap.add_argument("--threshold", type=float, default=0.25)
    ap.add_argument("--out", type=Path, default=Path("reports/drift_alert.json"))
    args = ap.parse_args()

    ref = pd.read_csv(args.reference)
    cur = pd.read_csv(args.current)
    results = compare(ref, cur, data.FEATURES)
    worst = max(results, key=lambda r: r.psi, default=None)
    max_psi = worst.psi if worst else 0.0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "threshold": args.threshold,
        "max_psi": max_psi,
        "worst_feature": worst.feature if worst else None,
        "results": [
            {
                "feature": r.feature,
                "psi": r.psi,
                "ks_statistic": r.ks_statistic,
                "verdict": r.verdict,
                "ref_mean": r.ref_mean,
                "cur_mean": r.cur_mean,
            }
            for r in results
        ],
    }
    args.out.write_text(json.dumps(payload, indent=2))

    if max_psi >= args.threshold:
        message = (
            f"Drift alert: {worst.feature} PSI={max_psi:.3f} exceeds the {args.threshold:.2f} threshold. "
            f"Review the upstream stream before retraining."
        )
        token = os.getenv("LINE_NOTIFY_TOKEN")
        sent = _send_line_notify(message, token)
        print(f"ALERT: {message}")
        return 0 if sent else 1

    print(f"No drift alert. Max PSI={max_psi:.3f} below threshold {args.threshold:.2f}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
