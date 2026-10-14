from __future__ import annotations

import smtplib

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
