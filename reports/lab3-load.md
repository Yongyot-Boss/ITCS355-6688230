# Lab 3 — serving, load, and rollback record

## Tasks 1–2: service, deployment, and smoke test

The provider-neutral FastAPI service implements `POST /predict`, `POST /predict/batch`,
`GET /health`, and `GET /ready`. Pydantic rejects malformed/extra input, all responses
include `model_version`, JSON request logs include `request_id`, `latency_ms`, and
`model_version`, and the model loads once in the FastAPI lifespan.

Verification: `13 passed` in `tests/test_service.py` and
`tests/test_cloudlayer_gcp_deploy.py` on 2026-09-28.

Vertex deployed MLflow registry v3 (`itcs355-6688230`) on `itcs355-lab3` using a
digest-pinned image and `n1-standard-2`. Live smoke predictions:

```json
{"case":1,"model_version":"3","probability":0.02808290717772469}
{"case":2,"model_version":"3","probability":0.2486138963897984}
{"case":3,"model_version":"3","probability":0.013739626760262942}
```

## Task 3: load test

Pre-measurement SLO: p95 below **200 ms** at **10 VUs**, with error rate below 1%.
The target was set before measurement in `loadtest/k6.js`.

The present (but not yet committed) `loadtest/locustfile.py` tested authenticated Vertex `rawPredict` for 60
seconds at each level. Raw evidence: `reports/lab3-vus{1,10,50}_stats.csv`.

| VUs | Requests | Throughput (req/s) | p50 (ms) | p95 (ms) | p99 (ms) | Error rate |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 74 | 1.39 | 570 | 1,100 | 5,200 | 0.00% |
| 10 | 95 | 1.62 | 1,600 | 15,000 | 16,000 | 0.00% |
| 50 | 570 | 9.68 | 2,700 | 8,700 | 9,200 | 0.00% |

The stated target is not met: the original `n1-standard-2` breaks at **1 VU** (p95
1,100 ms). The one-size-larger `n1-standard-4` test improved 1-VU p95 to **210 ms**,
but it still misses the 200 ms target. This is a warm, minimum-one-replica endpoint, so
no scale-to-zero cold-start value applies.

| Variable | Required comparison | Status |
|---|---|---|
| Batch size | one 100-row batch vs 100 single requests | Local service: 100-row batch p95 85 ms; single p95 87 ms at 9.39 req/s. 100 sequential singles take about 10.65 s at that throughput, so the batch is about 125x faster end-to-end. Vertex `rawPredict` is configured only for `/predict`, so it returns 422 when sent the batch contract; the batch route was measured directly. |
| Payload size | baseline vs larger valid JSON bodies | Vertex, 1 VU: baseline p95 1,100 ms vs valid 64 KiB padded JSON p95 370 ms, both 0% errors. The high baseline variance means this does **not** demonstrate that serialization improved; it demonstrates no observed 64 KiB serialization bottleneck in this run. |
| Instance size | `n1-standard-2` vs one size larger, including cost delta | Vertex, 1 VU: `n1-standard-4` p95 210 ms, 4.21 req/s, 0% errors. It costs about 6.3469 THB/hour versus 3.1735 THB/hour for `n1-standard-2`: +3.1735 THB/hour (+100%). |

## Task 4: canary and rollback

Production v3 held-out test ROC AUC was 0.85446; deliberately worse v4 was 0.84946.
Both replicas became healthy. Traffic was v3 `4035148300310020096` at 90% and v4
`8096269264291364864` at 10%; the larger-instance comparison replica remained at 0%.

- Metric: a 400-labelled-row rolling ROC AUC. The probe deliberately discarded every response's `model_version`.
- Baseline: v3 at 100% scored ROC AUC 0.860853 and Brier 0.084300 in 29.808 s (`reports/lab3-canary-v3-baseline.json`).
- Canary: the 90/10 window scored ROC AUC 0.858477 (a 0.002376 decline) and Brier 0.084246 in **27.922 s** (`reports/lab3-canary-v4-90-10.json`).
- Detection: the AUC decline was visible after the first 400-label, 27.922-second window; a pre-set 0.002 AUC guardrail and faster outcome labels would make alerting automatic and faster.
- Rollback: Vertex traffic was restored to v3 100%, v4 0%, larger replica 0%; endpoint `updateTime` is `2026-09-27T17:53:20.267540Z`. A 50/50 split would provide a stronger aggregate signal but expose five times more requests to v4.

## Task 5: cost per 1,000 predictions

At 10 VUs, achieved throughput was 1.615 req/s. Using the `n1-standard-2` on-demand
rate of USD 0.0949995/hour and the 25 September 2026 indicative exchange rate of
33.405 THB/USD, the hourly compute rate is **3.1735 THB/hour**. With explicit
utilisation assumption `U = 0.50`:

```text
THB / 1,000 predictions = 3.1735 / (1.615 × 3,600 × 0.50) × 1,000
                         = 1.0914 THB per 1,000 predictions
```

This is a compute-only planning estimate; Vertex management charges, storage, egress,
and currency movements are excluded. Reconcile it with the project Billing export.

For a one-hour daily batch run at the observed 1.615 req/s rate, batch capacity is about
5,815 predictions/day (174,462/month) and consumes 30 instance-hours/month rather than
720 for the warm endpoint. Thus batch is cheaper from 1 through about 5,815
predictions/day under that one-hour-run assumption. Above that volume it is still cheaper
whenever daily batch runtime remains below 24 instance-hours; actual batch duration has
not yet been measured.

## Teardown

After completing the missing comparisons and metric-only detection record, run
`make teardown TEARDOWN_LAB=3` and verify in Vertex AI that the endpoint/models are gone.
