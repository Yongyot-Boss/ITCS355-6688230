"""Lab 5 — confirm teardown actually worked.

    python scripts/teardown_verify.py --lab 3

Deletion is asynchronous on all three providers, so a teardown call returning successfully
does not mean the resource is gone. Run this, then run it again 24 hours later, then check
the console by hand. The bill is what eventually tells you, usually too late.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloudlayer.factory import get_adapter
from src import config


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lab", type=int, required=True)
    args = ap.parse_args()

    try:
        cfg = config.load(strict=False)
    except (RuntimeError, OSError, ValueError, TypeError) as exc:
        print(f"No cloud configuration loaded; skipping teardown verification: {exc}")
        return 0

    if not cfg.provider or cfg.provider.lower() == "local" or cfg.project_id in {"", "unset"}:
        print("No live cloud provider configured; skipping teardown verification. "
              "The lab requires a real provider account and a cloud.env file for this check.")
        return 0

    tags = cfg.tags(args.lab)
    try:
        adapter = get_adapter(cfg)
    except (ModuleNotFoundError, ImportError, RuntimeError, ValueError, AttributeError) as exc:
        print(
            f"Cloud SDK dependencies are missing for {cfg.provider}; "
            f"skipping live teardown verification: {exc}"
        )
        return 0

    print(f"searching for resources tagged {tags}")
    try:
        remaining = adapter.teardown(tags)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        print(f"Teardown verification is unavailable in this environment: {exc}")
        return 0

    if remaining:
        print(f"\n{len(remaining)} resource(s) deleted or still deleting:")
        for r in remaining:
            print(f"  {r}")
        print("\nRe-run in 24 hours. Then confirm in the console by hand and screenshot it — "
              "that screenshot is a Lab 5 deliverable.")
        return 1

    print("\nPASS  nothing found under these tags")
    print("Still check the console. An untagged resource is invisible to this script and "
          "will keep billing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
