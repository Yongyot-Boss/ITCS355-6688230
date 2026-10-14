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
        settings = _email_settings()
        sent = _send_email(message, **settings)
        print(f"ALERT: {message}")
        return 0 if sent else 1

    print(f"No drift alert. Max PSI={max_psi:.3f} below threshold {args.threshold:.2f}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
