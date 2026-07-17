# Execution-Efficiency Measurement

The framework should earn its complexity through measured reductions, not assumed
savings. Every comparable agent run should retain `run-metrics.json`; groups of runs
can then produce an `efficiency-comparison.json`.

## Run metrics

The OpenCode adapter records:

- context bytes, component count, task paths, and references;
- elapsed wall-clock milliseconds;
- tool calls and read calls;
- agent message and observed rework-event counts; and
- provider-reported input, output, reasoning, and cache tokens.

Token metrics are marked `unavailable` with null values when the provider does not
return usage. The framework does not estimate tokens from characters because that
would mix tokenizer assumptions with measured provider billing/context data.

```bash
esc-exec contract validate run-metrics \
  .execution/runs/run-001/run-metrics.json
```

The orchestrator exposes the artifact at `GET /runs/{id}/metrics`.

`rework_events` is an observed counter, not a quality score. The initial OpenCode
adapter emits zero for a first attempt; future retry and checkpoint-resume paths should
increment it when they can identify repeated work reliably.

## Compare cohorts

Compare multiple representative baseline and framework-assisted runs:

```bash
esc-exec measurement compare \
  --baseline baseline/run-1/run-metrics.json \
  --baseline baseline/run-2/run-metrics.json \
  --candidate candidate/run-1/run-metrics.json \
  --candidate candidate/run-2/run-metrics.json \
  --output efficiency-comparison.json
```

For each dimension, the report records baseline/candidate averages, sample counts,
direction, and percentage savings calculated as:

```text
(baseline average - candidate average) / baseline average * 100
```

Positive percentages are savings; negative percentages are regressions. A zero
baseline produces an unavailable percentage rather than division-based fiction.

## Experimental discipline

Compare like with like:

1. Use the same repository revision or document the revision difference.
2. Use equivalent task objectives and completion conditions.
3. Keep model, provider, permissions, and verification requirements constant.
4. Run enough samples to reduce cache, latency, and model variability.
5. Preserve failed runs; excluding them hides rework and reliability costs.
6. Report unavailable dimensions instead of substituting estimates.

Context bytes and tool calls explain navigation efficiency, while tokens and elapsed
time capture broader execution cost. Code reading, report diagnosis, and generation
may dominate some tasks, so improvement is expected to vary by task class.

## Current evidence boundary

The local OpenCode provider could not complete a real model-backed run during the
reference-adapter spike. The metrics and comparison pipeline are verified with
deterministic fixtures, but no real 10–20% token-savings claim is recorded yet. Build a
baseline cohort when a working provider is configured, then retain the comparison as
project evidence.
