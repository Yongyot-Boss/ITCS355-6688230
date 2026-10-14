from __future__ import annotations

import smtplib
import sys
from types import SimpleNamespace

import pytest

from monitoring import drift_alert


def test_send_email_uses_starttls_and_configured_recipient(monkeypatch):
    sent = {}

    class SMTP:
        def __init__(self, host, port, timeout):
            sent["connection"] = (host, port, timeout)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def starttls(self, *, context):
            sent["tls_context"] = context

        def login(self, username, password):
            sent["credentials"] = (username, password)

        def send_message(self, message):
            sent["message"] = message

    monkeypatch.setattr(drift_alert.smtplib, "SMTP", SMTP)

    assert drift_alert._send_email(
        "temp_c PSI=0.383 crossed threshold",
        host="smtp.example.test",
        port=587,
        username="alert-user",
        password="secret",
        sender="alerts@example.test",
        recipient="on-call@example.test",
    )

    assert sent["connection"] == ("smtp.example.test", 587, 20)
    assert sent["credentials"] == ("alert-user", "secret")
    assert sent["message"]["To"] == "on-call@example.test"
    assert "temp_c PSI=0.383" in sent["message"].get_content()


def test_email_settings_fail_when_required_configuration_is_missing(monkeypatch):
    for name in (
        "SMTP_HOST",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "DRIFT_EMAIL_FROM",
        "DRIFT_EMAIL_TO",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="SMTP_HOST.*SMTP_USERNAME"):
        drift_alert._email_settings()


def test_send_email_reports_smtp_failure(monkeypatch, capsys):
    class SMTP:
        def __init__(self, *args, **kwargs):
            raise smtplib.SMTPException("relay unavailable")

    monkeypatch.setattr(drift_alert.smtplib, "SMTP", SMTP)

    assert not drift_alert._send_email(
        "alert",
        host="smtp.example.test",
        port=587,
        username="alert-user",
        password="secret",
        sender="alerts@example.test",
        recipient="on-call@example.test",
    )
    assert "email delivery failed" in capsys.readouterr().out


def test_breach_emits_metric_and_sends_email(monkeypatch, tmp_path):
    metrics = []
    emails = []
    adapter = SimpleNamespace(emit_metric=lambda name, value: metrics.append((name, value)))
    result = SimpleNamespace(
        feature="temp_c",
        psi=0.4,
        ks_statistic=0.3,
        verdict="significant",
        ref_mean=79.5,
        cur_mean=85.5,
    )
    monkeypatch.setattr(drift_alert.pd, "read_csv", lambda _: object())
    monkeypatch.setattr(drift_alert, "compare", lambda *_: [result])
    monkeypatch.setattr(drift_alert, "_email_settings", lambda: {"host": "smtp"})
    monkeypatch.setattr(
        drift_alert,
        "_send_email",
        lambda message, **settings: emails.append((message, settings)) or True,
    )
    monkeypatch.setattr(
        "cloudlayer.factory.get_adapter",
        lambda config: adapter,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "drift_alert.py",
            "--reference",
            str(tmp_path / "reference.csv"),
            "--current",
            str(tmp_path / "current.csv"),
            "--out",
            str(tmp_path / "alert.json"),
            "--threshold",
            "0.25",
            "--emit",
        ],
    )

    assert drift_alert.main() == 0
    assert metrics == [("drift.psi.temp_c", 0.4)]
    assert "temp_c PSI=0.400" in emails[0][0]


def test_gcs_sources_download_through_adapter(monkeypatch, tmp_path):
    downloads = []
    metrics = []
    adapter = SimpleNamespace(
        download=lambda uri, destination: downloads.append((uri, destination)),
        emit_metric=lambda name, value: metrics.append((name, value)),
    )
    result = SimpleNamespace(
        feature="temp_c",
        psi=0.1,
        ks_statistic=0.05,
        verdict="moderate",
        ref_mean=79.5,
        cur_mean=80.0,
    )
    monkeypatch.setattr(drift_alert.pd, "read_csv", lambda _: object())
    monkeypatch.setattr(drift_alert, "compare", lambda *_: [result])
    monkeypatch.setattr("cloudlayer.factory.get_adapter", lambda config: adapter)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "drift_alert.py",
            "--reference-gcs-uri",
            "bucket://reference.csv",
            "--current-gcs-uri",
            "bucket://current.csv",
            "--out",
            str(tmp_path / "alert.json"),
            "--emit",
        ],
    )

    assert drift_alert.main() == 0
    assert [uri for uri, _ in downloads] == [
        "bucket://reference.csv",
        "bucket://current.csv",
    ]
    assert len(metrics) == 1


def test_synthetic_drift_mode_shifts_reference_and_sends_email(monkeypatch, tmp_path):
    baseline = drift_alert.pd.DataFrame({"temp_c": [70.0, 80.0]})
    downloads = []
    adapter = SimpleNamespace(
        download=lambda uri, destination: downloads.append(uri),
        emit_metric=lambda name, value: None,
    )
    result = SimpleNamespace(
        feature="temp_c",
        psi=0.4,
        ks_statistic=0.3,
        verdict="significant",
        ref_mean=75.0,
        cur_mean=81.0,
    )
    monkeypatch.setattr(drift_alert.pd, "read_csv", lambda _: baseline.copy())

    def compare(reference, current, features):
        assert current["temp_c"].tolist() == [76.0, 86.0]
        return [result]

    monkeypatch.setattr(drift_alert, "compare", compare)
    monkeypatch.setattr("cloudlayer.factory.get_adapter", lambda config: adapter)
    monkeypatch.setattr(drift_alert, "_email_settings", lambda: {"host": "smtp"})
    emails = []
    monkeypatch.setattr(
        drift_alert,
        "_send_email",
        lambda message, **settings: emails.append(message) or True,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "drift_alert.py",
            "--reference-gcs-uri",
            "bucket://reference.csv",
            "--test-synthetic-drift",
            "--emit",
            "--out",
            str(tmp_path / "alert.json"),
        ],
    )

    assert drift_alert.main() == 0
    assert downloads == ["bucket://reference.csv"]
    assert "temp_c PSI=0.400" in emails[0]
