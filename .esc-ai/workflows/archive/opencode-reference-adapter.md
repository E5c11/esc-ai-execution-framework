# OpenCode Reference Adapter
**Status:** Complete with local provider limitation
**Roadmap capability:** 4

## Deliverables

- [x] Installed OpenCode API inspected (`0.15.30`)
- [x] Repository resolution and index routing
- [x] Read-only OpenCode tool-policy mapping
- [x] Session creation and session resumption input
- [x] Live session fork
- [x] Portable run/event/artifact translation
- [x] Explicit provider/model configuration
- [x] Provider errors translated into failed runs
- [x] Mocked successful provider conformance tests
- [x] Usage guide

## Verification

- 24 framework tests pass.
- Mocked successful OpenCode responses produce valid portable run, event, and artifact
  contracts and include the routed component index in the prompt.
- The live server resolved `ampm-backend`, created sessions, accepted prompts, returned
  structured provider errors, and successfully forked a session.
- A fully successful live model response could not be demonstrated on this machine:
  installed OpenCode advertises `grok-code` and `big-pickle`; the former returns “model
  not supported” and the latter returns OpenCode's `DecimalError`. This is external model
  availability, not hidden as adapter success.

## Completion rationale

The adapter/server boundary, policy mapping, failure behavior, output contracts, resume
input, and fork operation are exercised. Capability 5 can now decide whether this thin
adapter is enough for the first orchestrator or whether a newer OpenCode deployment is
required for production runs.
