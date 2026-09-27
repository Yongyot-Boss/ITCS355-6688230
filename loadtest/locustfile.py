"""ITCS355 Lab 3 — load test (Locust alternative to k6).

    locust -f loadtest/locustfile.py --host https://<endpoint> \
           --users 10 --spawn-rate 5 --run-time 60s --headless

Use either this or k6, not both. Whichever you choose, commit it.
"""
from __future__ import annotations

import random
import json
import os

from locust import HttpUser, between, task


def sample_payload() -> dict:
    return {
        "temp_c": round(random.uniform(60, 95), 3),
        "vibration_mm_s": round(random.uniform(1.0, 8.0), 3),
        "pressure_kpa": round(random.uniform(280, 350), 3),
        "hours_since_service": round(random.uniform(0, 9000), 3),
        "load_pct": round(random.uniform(20, 100), 3),
        "ambient_humidity": round(random.uniform(30, 85), 3),
    }


class PredictUser(HttpUser):
    wait_time = between(0.0, 0.1)

    def on_start(self) -> None:
        """Support either a direct service URL or authenticated Vertex rawPredict.

        For Vertex, set PREDICT_PATH to the endpoint's ``:rawPredict`` path and
        VERTEX_TOKEN to a short-lived OAuth access token.  Vertex forwards the body
        unchanged to /predict, so this is a real service load test rather than a
        test of a local proxy.
        """
        self.predict_path = os.environ.get("PREDICT_PATH", "/predict")
        token = os.environ.get("VERTEX_TOKEN")
        self.headers = {"Content-Type": "application/json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.mode = os.environ.get("TASK_MODE", "mixed")
        self.pad_bytes = int(os.environ.get("PAD_BYTES", "0"))

    def _post(self, body: dict, name: str):
        # JSON whitespace is valid and reaches the parser unchanged; it lets us
        # isolate serialization cost without weakening the strict request schema.
        encoded = json.dumps(body, separators=(",", ":")) + (" " * self.pad_bytes)
        return self.client.post(
            self.predict_path,
            data=encoded,
            headers=self.headers,
            name=name,
            catch_response=True,
        )

    @task(9)
    def predict(self):
        if self.mode == "batch":
            # Keep batch-only runs meaningful: this weighted task still has to
            # issue a batch request rather than silently consuming a schedule slot.
            return self._predict_batch()
        with self._post(sample_payload(), "predict") as r:
            if r.status_code != 200:
                r.failure(f"status {r.status_code}")

    @task(1)
    def predict_batch(self):
        if self.mode == "single":
            # Likewise, every scheduled task in a single-only control run must
            # be a real single prediction.
            return self.predict()
        return self._predict_batch()

    def _predict_batch(self):
        rows = [sample_payload() for _ in range(100)]
        # The report compares this with 100 single calls at the same concurrency.
        with self._post({"rows": rows}, "predict_batch") as r:
            if r.status_code != 200:
                r.failure(f"status {r.status_code}")
