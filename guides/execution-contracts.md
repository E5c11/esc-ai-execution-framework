# Provider-neutral Execution Contracts

These contracts are the portable boundary between consuming repositories, agent
runtimes such as OpenCode, and a future central orchestrator.

## Lifecycle

```text
Task specification
  -> Run bound to an adapter, workspace, and policy
  -> Ordered JSONL events and artifact records
  -> Terminal run result or durable checkpoint
```

Provider-specific identifiers and raw responses belong under `adapter_metadata`; they
must not replace portable IDs or lifecycle states.

## Formats and ownership

| Contract | Format | Typical owner | Storage |
|---|---|---|---|
| Task specification | YAML | Human/project | Committed with active workflow |
| Workspace | YAML | Project/operator | Committed when reusable; local override allowed |
| Adapter | YAML | Framework/operator | Committed; contains no credentials |
| Policy | YAML | Project/operator | Committed |
| Run | JSON | Orchestrator | Transient or external persistence |
| Event stream | JSONL | Orchestrator/adapter | Transient or external persistence |
| Artifact record | JSON | Orchestrator | Follows artifact retention policy |
| Checkpoint | YAML | Agent/human | Committed when needed for resumption |

Secrets, API keys, absolute developer paths, and full model transcripts must not be
stored in committed contracts.

## Validation

```bash
esc-exec contract validate task .esc-ai/workflows/active/example/task.yaml
esc-exec contract validate run .esc-ai/runs/run-001/run.json
esc-exec contract validate event .esc-ai/runs/run-001/events.jsonl
esc-exec contract validate-set examples/contracts
```

The connected examples under `examples/contracts/` demonstrate all eight contract
types. Schema version `1` is deliberately small; later capabilities may add optional
verification, reporting, dependency-impact, and checkpoint detail without coupling the
base lifecycle to an agent provider.

## Adapter boundary

An adapter translates provider behavior into these contracts. For OpenCode, an adapter
will map OpenCode sessions and server events into portable runs and events, retain the
OpenCode session ID only as adapter metadata, and apply the portable policy before
provider permissions are configured.
