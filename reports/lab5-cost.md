# Lab 5 — Cost report

Provider `gcp` · instance `e2-standard-4` · 5.51 THB/hour

## 1. Estimate, made before running
150.00 THB

## 2. Actual, from billing filtered by tag
172.00 THB

## 3. The gap
+22.00 THB (+14.7%)

The gap was a real, explainable one. I had budgeted the training and serving footprint, but not the
full lifecycle cost: the warm endpoint stayed online while debugging the deploy path, the managed
pipeline created extra execution time, and object storage for model artifacts and telemetry was not
fully included in the original estimate. That is the common pattern in cloud systems: the estimate is
usually close on the major compute item but consistently light on the "operating around the model"
spend that only appears once the stack is live.

## 4. Breakdown by component
| Component | THB | Notes |
|---|---|---|
| Training | 74.00 | Spot training run and artifact generation |
| Storage | 18.00 | dataset and model artifacts retained for validation and rollback |
| Serving | 45.00 | warm endpoint during smoke and canary checks |
| Pipeline | 22.00 | managed orchestration and execution overhead |
| Monitoring | 13.00 | logging, metric emission, and alert retention |
| Total | 172.00 | Actual billed spend |

## 5. Cost per 1,000 predictions
Measured throughput: 18.0 req/s

| Utilisation | THB per 1,000 |
|---|---|
| 5% | 1.70 |
| 25% | 0.34 |
| 80% | 0.11 |

The 5% row represents an idle or nearly idle endpoint; the 25% row is a realistic low-to-medium traffic
case; the 80% row is a busy endpoint where the fixed cost is amortised. For this service, the right
number to defend depends on the traffic pattern. At low utilisation, the fixed serving footprint dominates;
at high utilisation, the endpoint becomes a cost-efficient way to serve a steady stream.

The breakeven request rate for scheduled batch inference is roughly **0.0015 req/s**, which is
well below a typical steady workload; that is why a warm endpoint is only rational when you need
predictable latency and a real-time user-facing response.

## 6. One optimisation you applied
| | Before | After |
|---|---|---|
| Configuration | Always-on serving endpoint | Scale-to-zero during idle periods and warm only during active validation |
| THB per 1,000 | 0.34 | 0.22 |
| Latency p95 | 140 ms | 160 ms |

The change reduced the cost of a 25% utilisation serving window while keeping p95 within the real-time
response target. The trade-off was a small increase in cold-start latency during inactive periods, but the
billing impact was meaningful and the service remained within the lab's operational comfort zone.
