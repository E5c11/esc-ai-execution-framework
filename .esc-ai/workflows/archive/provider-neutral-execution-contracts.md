# Provider-neutral Execution Contracts
**Status:** Complete
**Roadmap capability:** 3

## Outcome

Define and validate the portable boundary that OpenCode and future runtimes implement.

## Deliverables

- [x] Task specification schema
- [x] Run lifecycle schema
- [x] Ordered JSONL event schema
- [x] Artifact schema and retention classification
- [x] Durable checkpoint schema
- [x] Workspace, adapter, and policy schemas
- [x] CLI validation for all contract types
- [x] Connected conformance examples
- [x] Contract lifecycle and ownership guide
- [x] Final test and schema verification

## Completion Gate

1. All eight connected examples validate.
2. Invalid lifecycle states and non-monotonic events fail validation.
3. Existing manifest/index tests remain green.
4. No OpenCode-specific field appears outside generic adapter configuration or metadata.
5. Capability 4 can implement the contracts without changing their base lifecycle.

## Completion Evidence

- `python -m unittest discover -v`: 22 tests passed.
- All eight individual conformance examples validate.
- `esc-exec contract validate-set examples/contracts` validates the connected task,
  bindings, run, events, artifact, and checkpoint references.
- Regression tests reject invalid lifecycle states, broken cross-contract references,
  non-monotonic event streams, and missing contract files.
- OpenCode appears only as the example adapter provider and provider session metadata;
  no portable lifecycle field depends on it.
