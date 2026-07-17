# OpenCode Reference Adapter

The OpenCode adapter is the first implementation of the provider-neutral execution
contracts. It targets OpenCode's headless HTTP server and keeps provider session IDs
inside `run.adapter_metadata`.

## Start a server

```bash
opencode serve --hostname 127.0.0.1 --port 4097
```

## Execute a task

```bash
esc-exec opencode execute \
  examples/contracts/task.yaml \
  examples/contracts/workspace.yaml \
  examples/contracts/adapter.yaml \
  examples/contracts/policy.yaml \
  --server http://127.0.0.1:4097 \
  --output /tmp/esc-opencode-runs
```

The adapter resolves the task repository through the local registry, matches its root
index, creates or resumes a session, and writes portable `run.json`, `events.jsonl`,
`artifact.json`, and `summary.json` outputs.

Read-only policy disables `bash`, `edit`, `write`, `patch`, `webfetch`, tasks, and todo
tools while allowing `read`, `list`, `glob`, and `grep`.

## Resume and fork

Pass `--session <provider-session-id>` to execute another run in an existing OpenCode
session. Fork independently with:

```bash
esc-exec opencode fork ampm-backend <provider-session-id> --server http://127.0.0.1:4097
```

## Provider errors

Assistant error metadata and empty assistant responses produce portable failed runs;
they are never reported as successful. Model selection is explicit in the adapter
contract under `adapter.configuration.model`.

