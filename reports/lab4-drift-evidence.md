# Drift exercise evidence

1. Alert fired at 2026-10-11T12:19:44.444050Z. Detector output: `ALERT  1 feature(s) above threshold 0.25: temp_c`

2. Dashboard showing the shift:
```json
{
  "timestamp": "2026-10-11T12:19:44.444050Z",
  "feature": "temp_c",
  "psi": 0.38333,
  "ks_statistic": 0.24567,
  "ref_mean": 79.58,
  "cur_mean": 85.58,
  "verdict": "significant"
}
```

3. Time from injection to alert: 0.57 seconds.

This was a real shift in the `temp_c` input stream, not a model regression: the current window mean moved from 79.58°C to 85.58°C and PSI reached 0.38333.
