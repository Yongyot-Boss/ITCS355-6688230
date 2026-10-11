"""Fail the build if secret-like material exists in git history.

The lab requires this to run against a full checkout (`fetch-depth: 0` in GitHub Actions).
A shallow checkout hides earlier commits and makes a secret scan meaningless because the
repository may appear clean even when a secret was already exposed in history.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    (r"AKIA[0-9A-Z]{16}", "AWS access key"),
    (r"ASIA[0-9A-Z]{16}", "AWS temporary access key"),
    (r"gh[pousr]_[A-Za-z0-9_]{20,}", "GitHub token"),
    (r"github_pat_[A-Za-z0-9_]{20,}", "GitHub PAT"),
    (r"xox[baprs]-[A-Za-z0-9-]+", "Slack token"),
    (r"AIza[0-9A-Za-z_-]{35}", "GCP API key"),
    (r"sk_live_[A-Za-z0-9]+", "Stripe live key"),
    (r"-----BEGIN (RSA|DSA|EC|OPENSSH|PGP) PRIVATE KEY-----", "private key"),
    (r"aws_secret_access_key[[:space:]]*[:=][[:space:]]*['\"]?[A-Za-z0-9/+=]{40}", "AWS secret"),
    (r"password[[:space:]]*[:=][[:space:]]*['\"]?[A-Za-z0-9!@#$%^&*()_+=-]{8,}['\"]?", "password assignment"),
]


def run_git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def main() -> int:
    shallow = run_git("rev-parse", "--is-shallow-repository")
    if shallow.returncode != 0:
        print("SECRET SCAN FAILED — unable to determine repository depth.")
        return 1
    if shallow.stdout.strip() == "true":
        print("SECRET SCAN FAILED — repository checkout is shallow; run GitHub Actions with fetch-depth: 0.")
        return 1

    refs = run_git("rev-list", "--all")
    if refs.returncode != 0:
        print("SECRET SCAN FAILED — unable to enumerate repository history.")
        return 1

    commit_list = [ref for ref in refs.stdout.splitlines() if ref.strip()]
    if not commit_list:
        print("SECRET SCAN PASSED — no reachable history to scan.")
        return 0

    hits: list[str] = []
    for pattern, label in PATTERNS:
        grep = subprocess.run(
            ["git", "grep", "-nE", "-I", "-e", pattern, *commit_list],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if grep.returncode not in (0, 1):
            print(grep.stderr.strip() or f"SECRET SCAN FAILED — git grep error for {label}.")
            return 1
        if grep.returncode == 0:
            for line in grep.stdout.strip().splitlines():
                if line.strip():
                    hits.append(f"[{label}] {line}")

    if not hits:
        print("SECRET SCAN PASSED — no suspicious secret-like content found in git history.")
        return 0

    print("SECRET SCAN FAILED — suspicious secret-like content found in git history:\n")
    for line in hits[:50]:
        print(f"  {line}")
    if len(hits) > 50:
        print(f"  ... and {len(hits) - 50} more matches")
    print("\nRemove the secret from history and rotate it immediately.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
