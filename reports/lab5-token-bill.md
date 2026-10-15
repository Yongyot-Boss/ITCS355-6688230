# Lab 5 Task 9 — token bill

This report uses the recorded baseline responses in `evals/fixtures/triage-baseline.jsonl` and the pricing model in `src/llmcost.py`.

## 1) THB per 1,000 requests at measured usage

The average request in the baseline fixture is:

- input tokens = 416
- output tokens = 37
- model = `gcp/small`

Using the provider pricing table in `src/llmcost.py`:

- input rate = 8.5 THB per 1,000,000 tokens
- output rate = 34.0 THB per 1,000,000 tokens

Single-request cost:

```python
from src.llmcost import Usage, request_cost
usage = Usage(input_tokens=416, output_tokens=37)
request_cost("gcp", "small", usage)
# 0.004794 THB/request
```

Therefore:

```python
0.004794 * 1000 = 4.794 THB per 1,000 requests
```

Result: 4.794 THB / 1,000 requests.

## 2) Savings from capping `max_output_tokens`

The largest operational lever is output length. I tested a shorter output profile that still passes the same golden-set gate (`evals/fixtures/triage-capped.jsonl`). The output cap stays below the guardrail and the gate still passes:

```bash
python scripts/llm_eval.py \
  --responses evals/fixtures/triage-capped.jsonl \
  --out reports/llm_eval-capped.json \
  --baseline reports/llm_eval-baseline.json
```

This passes with:

- pass rate: 8/8
- cost: 3.978 THB / 1,000 requests

The cap was set to 20 output tokens for the capped fixture. The savings are:

```python
from src.llmcost import Usage, output_cap_saving
usage = Usage(input_tokens=416, output_tokens=37)
output_cap_saving("gcp", "small", usage, 20)
# 0.578 THB per 1,000 requests saved
```

So capping to 20 output tokens reduces the bill from 4.794 THB to 4.216 THB per 1,000 requests, saving 0.578 THB per 1,000 requests while keeping the gate green. The cheaper system is only a real optimisation if the evaluation gate still passes, which it does here.

## 3) Prompt-cache break-even hit rate

The prompt cache is a good example of a lever that looks attractive but only pays off when the hit rate is high enough. The formula in `src/llmcost.py` gives the cache break-even point:

```python
from src.llmcost import cache_breakeven_hit_rate
cache_breakeven_hit_rate("gcp", "small", prefix_tokens=2000)
# 0.217391...
```

So the break-even hit rate is about 21.7%.

Our actual measured hit rate is effectively 0% because the system is not yet using prompt caching. That means the cache is below the breakeven point today, and a cache write would cost more than the saved read tokens. The system should not turn on a prompt cache until the hit rate is above ~21.7% for the same prefix.

## Budget defence

At the measured baseline, the LLM step costs 4.79 THB per 1,000 requests, which is small compared with the serving and training overhead but still a real operating cost because the model call runs on every triage decision. I reduced it by capping output length to 20 tokens while keeping the golden-set gate green; that cut the cost to 4.22 THB per 1,000 requests and saved 0.58 THB per 1,000 requests without degrading the decisions our evaluation checks. I did not enable prompt caching because the measured hit rate is 0% and the break-even rate is about 21.7%, meaning a cache would add cost rather than reduce it under the current workload. In plain terms: we keep the smaller, tighter output and wait until the prefix reuse is high enough before paying the cache-write premium.
