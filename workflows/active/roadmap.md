# ESC AI Execution Framework — Active Roadmap
**Status:** Active — capabilities are being implemented sequentially
**Goal:** Establish portable execution contracts, prove them through OpenCode, and then
build the efficiency capabilities one vertical slice at a time.

---

## Settled Foundations

- The framework is provider-agnostic and separate from the ESC AI Engineering
  Framework.
- Industry terminology is preferred: repository manifest, component manifest, task
  specification, run, event, artifact, checkpoint, workspace, adapter, and policy.
- Component manifests are colocated with and owned by the modules/packages they
  describe.
- A committed root `esc-execution.yaml` explicitly declares component manifests at
  `<component>/esc-component.yaml`.
- A machine-local route registry resolves stable repository/framework IDs to absolute
  checkout paths and flags missing or stale registrations.
- Reliably derivable structural facts are generated; semantic facts are human-authored.
- Missing or incomplete manifests trigger generation before bounded manual fallback.
- Durable framework-related task state is committed to the consuming repository;
  transient logs, caches, and scratch data are not.
- Documents carry an explicit integer `schema_version` from their first version.
- OpenCode is the initial lightweight reference runtime. Portable contracts must not
  depend on OpenCode-specific concepts.
- The central control plane lives in the separate `esc-ai-orchestrator` repository and
  consumes this framework's portable contracts.

## Sequence

| # | Capability | Outcome | Status |
|---|---|---|---|
| 1 | [Component manifests and route discovery](../archive/component-manifests-and-route-discovery.md) | Schemas, setup instructions, local registry convention, generation/validation behavior | Complete |
| 2 | [Repository and component indexes](../archive/repository-component-indexes.md) | Manifest-driven routing catalog generated from declared components and repository structure | Complete |
| 3 | [Provider-neutral execution contracts](../archive/provider-neutral-execution-contracts.md) | Task specification, run, event, artifact, checkpoint, workspace, adapter, and policy schemas | Complete |
| 4 | [OpenCode reference adapter](../archive/opencode-reference-adapter.md) | Resolve a repo, load manifests, start/observe/resume a small OpenCode-backed run | Complete — local models unavailable |
| 5 | Central orchestrator bootstrap | Separate control-plane repository with HTTP submission/observation, SQLite persistence, scheduling, and a replaceable runtime boundary | Complete |
| 6 | [Compact test and report summaries](../archive/compact-verification-reports.md) | Bounded structured verification results with full artifacts retained | Complete |
| 7 | Task context and progressive verification | Task-specific context plus focused → component → impact → final gates | **Next** |
| 8 | Executable architecture checks | Deterministic rule enforcement with stable rule IDs | Pending |
| 9 | Durable checkpoints | Another agent/person can resume incomplete work without reconstructing history | Pending |
| 10 | Dependency graph and impact analysis | Generated consumer relationships drive appropriate verification scope | Pending |
| 11 | Execution-efficiency measurement | Evidence for token, tool-call, elapsed-time, and rework improvements | Pending |

## Capability 1 — Initial Scope

The first capability must decide and implement only the foundation needed for manifest
adoption:

1. Define repository-manifest and component-manifest schemas.
2. Define `esc-execution.yaml` and `<component>/esc-component.yaml` discovery rules.
3. Define the machine-local route-registry schema and platform path convention.
4. Document how a repository and component adopt the framework.
5. Specify missing, incomplete, stale, and invalid states.
6. Generate reliably detectable structure for one real repository.
7. Validate generated facts without overwriting human-authored semantics.
8. Exercise the result against `ampm-backend` before generalizing it.

The implementation language should be chosen during this capability based on the
requirements of manifest parsing, generation, validation, portability, and eventual
OpenCode integration.

## OpenCode Reference Spike

Capability 4 should prove the smallest useful integration rather than build the full
orchestrator. The spike succeeds when it can:

1. Resolve a registered repository ID.
2. Load its repository and matching component manifests.
3. Start a controlled read-only task through OpenCode's server/API.
4. Stream or retrieve structured run events.
5. Stop the run and retain its artifacts.
6. Resume or fork the session where supported.
7. Demonstrate that the framework contracts do not expose OpenCode-specific fields.

## Orchestrator Boundary

The framework repository owns specifications, schemas, conventions, conformance
fixtures, reusable adapters, and small generic tooling. The orchestrator owns
the running control plane: API, persistence, scheduling, credentials, workspaces,
agent selection, concurrency, approvals, and deployment.

The bootstrap preserves explicit store, scheduler, and runtime boundaries so its
SQLite database and in-process worker can be replaced without changing the portable
execution contracts.
