# Compact Verification Reports

Agents should read a bounded verification summary first and open the complete report
only when a failure needs deeper diagnosis. The full report remains authoritative;
the summary is generated, disposable, and must never replace it.

## Component-owned profiles

Each component that emits reports owns its profile beside `esc-component.yaml`. Use
`esc-report-profile.yaml` as the canonical filename and declare it in the component
manifest so discovery does not require a recursive search:

```yaml
paths:
  source: src/main
  tests: src/test
  report_profile: esc-report-profile.yaml
```

Start from `examples/report-profiles/junit.yaml`. The profile selects the parser and
sets hard bounds for the number of failures and message characters included. A
component may choose tighter limits when its reports are especially noisy.

If a declared profile is missing or incomplete, request or generate it before reading
the full report. Do not silently use a global fallback: that would make context size
unpredictable and hide a component's verification convention.

## Generate a summary

JUnit XML is the first portable input format because it is supported by build and test
tools across ecosystems:

```bash
esc-exec report summarize \
  component/esc-report-profile.yaml \
  component/build/test-results/test/TESTS-TestSuites.xml \
  .execution/runs/run-001/verification-summary.json \
  --full-report-path component/build/test-results/test/TESTS-TestSuites.xml
```

The generated JSON contains status, aggregate counts, duration, a bounded failure
sample, explicit omission counts, and a workspace-relative pointer to the retained
XML. Validate it with:

```bash
esc-exec contract validate verification-summary \
  .execution/runs/run-001/verification-summary.json
```

The orchestrator exposes the same document at `GET /runs/{id}/summary` when a runtime
writes `verification-summary.json` into its output directory.

## Limits of the first slice

This parser reads individual JUnit test cases and does not yet merge multiple report
files, summarize compiler output, coverage, static analysis, or build logs. Add those
as source-format adapters behind the same bounded summary principle rather than
embedding tool-specific output in orchestration contracts.
