"""Scheduled drift alerting for Lab 5.

This script keeps the monitoring loop real without requiring a provider-specific SDK:
- it compares the current production snapshot against the training reference window,
- writes the score to a local metrics file so dashboards can read it,
- sends an email alert through a configured SMTP relay.
"""
from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
import sys
import tempfile
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from monitoring.drift import compare
from src import data


def _send_email(
    message: str,
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    sender: str,
    recipient: str,
) -> bool:
    email = EmailMessage()
    email["Subject"] = "ITCS355 production feature drift alert"
    email["From"] = sender
    email["To"] = recipient
    email.set_content(message)
    try:
        with smtplib.SMTP(host, port, timeout=20) as client:
            client.starttls(context=ssl.create_default_context())
            client.login(username, password)
            client.send_message(email)
        print(f"Drift alert email sent to {recipient}.")
        return True
    except (OSError, smtplib.SMTPException) as exc:
        print(f"Drift alert email delivery failed: {exc}")
        return False


def _email_settings() -> dict[str, str | int]:
    required = {
        "SMTP_HOST": os.getenv("SMTP_HOST"),
        "SMTP_USERNAME": os.getenv("SMTP_USERNAME"),
        "SMTP_PASSWORD": os.getenv("SMTP_PASSWORD"),
        "DRIFT_EMAIL_FROM": os.getenv("DRIFT_EMAIL_FROM"),
        "DRIFT_EMAIL_TO": os.getenv("DRIFT_EMAIL_TO"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(
            "Email alert is enabled by a drift breach but SMTP configuration is missing: "
            + ", ".join(missing)
        )
    try:
        port = int(os.getenv("SMTP_PORT", "587"))
    except ValueError as exc:
        raise RuntimeError("SMTP_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("SMTP_PORT must be between 1 and 65535")
    return {
        "host": required["SMTP_HOST"] or "",
        "port": port,
        "username": required["SMTP_USERNAME"] or "",
        "password": required["SMTP_PASSWORD"] or "",
        "sender": required["DRIFT_EMAIL_FROM"] or "",
        "recipient": required["DRIFT_EMAIL_TO"] or "",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", type=Path, default=Path("data/raw/sensors.csv"))
    ap.add_argument("--reference-gcs-uri")
    ap.add_argument("--current", type=Path)
    ap.add_argument("--current-gcs-uri")
    ap.add_argument(
        "--test-synthetic-drift",
        action="store_true",
        help="shift temp_c in a copy of the reference data to exercise the alert path",
    )
    ap.add_argument("--threshold", type=float, default=0.25)
    ap.add_argument("--out", type=Path, default=Path("reports/drift_alert.json"))
    ap.add_argument("--emit", action="store_true", help="write each feature's PSI as a metric")
    args = ap.parse_args()

    if args.test_synthetic_drift and (args.current or args.current_gcs_uri):
        ap.error("--test-synthetic-drift cannot be combined with a current data source")
    if not args.test_synthetic_drift and bool(args.current) == bool(args.current_gcs_uri):
        ap.error("provide exactly one of --current or --current-gcs-uri")
    if args.reference_gcs_uri and args.reference != Path("data/raw/sensors.csv"):
        ap.error("provide only one reference source: --reference or --reference-gcs-uri")

    adapter = None
    if args.reference_gcs_uri or args.current_gcs_uri or args.emit:
        from cloudlayer.factory import get_adapter
        from src import config

        adapter = get_adapter(config.load(strict=False))

    with tempfile.TemporaryDirectory(prefix="drift-alert-") as temp_dir:
        reference_path = args.reference
        current_path = args.current
        if args.reference_gcs_uri:
            assert adapter is not None
            reference_path = Path(temp_dir) / "reference.csv"
            adapter.download(args.reference_gcs_uri, str(reference_path))
        if args.current_gcs_uri:
            assert adapter is not None
            current_path = Path(temp_dir) / "current.csv"
            adapter.download(args.current_gcs_uri, str(current_path))

        ref = pd.read_csv(reference_path)
        if args.test_synthetic_drift:
            cur = ref.copy()
            cur["temp_c"] = cur["temp_c"] + 6.0
            print("TEST MODE: shifted temp_c by +6.0 from the reference data.")
        else:
            assert current_path is not None
            cur = pd.read_csv(current_path)
        results = compare(ref, cur, data.FEATURES)

        if args.emit:
            assert adapter is not None
            for result in results:
                adapter.emit_metric(f"drift.psi.{result.feature}", result.psi)

        worst = max(results, key=lambda r: r.psi, default=None)
        max_psi = worst.psi if worst else 0.0
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

        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2))

        if max_psi >= args.threshold:
            message = (
                f"Drift alert: {worst.feature} PSI={max_psi:.3f} exceeds the {args.threshold:.2f} threshold. "
                f"Review the upstream stream before retraining."
            )
            settings = _email_settings()
            sent = _send_email(message, **settings)
            print(f"ALERT: {message}")
            if not sent:
                return 1
            print("Drift threshold breached and email notification delivered.")
            return 0

        print(f"No drift alert. Max PSI={max_psi:.3f} below threshold {args.threshold:.2f}.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
