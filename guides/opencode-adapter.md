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
  --server http://127.0.0.1:4097
```

The adapter resolves the task repository through the local registry, matches its root
index, creates or resumes a session, and writes portable `run.json`, `events.jsonl`,
`artifact.json`, and `summary.json` outputs to `<repository>/.esc-ai/runs/<run-id>/` —
resolved from the task's `repository` field via the registry, not a location the caller
chooses.

The OpenCode tool grant for the run is derived from the policy document's
`permissions` (see `esc_exec.opencode_adapter.tools_for_policy`): `read` maps to
`read`/`list`/`glob`/`grep`, `edit` to `edit`/`write`/`patch`, `execute` to `bash`,
`network` to `webfetch` — granted only when the category is exactly `allow`.
`examples/contracts/policy.yaml` ("readonly-review") happens to compute to a
read-only grant, but it's one example policy, not the adapter's only possible
behavior — a policy declaring `edit: allow` produces a run that can actually edit
files. A permission valued `ask` is treated as denied (there's no mid-run
human-escalation path yet), and `external_paths`/`limits`/`approvals` are not
enforced by this mapping at all — they need path-scoping and run-duration/approval
gating, a different mechanism. The tool grant actually used is recorded at
`run.bindings.tool_grant` in the run's `run.json`, so it's auditable after the fact
rather than only inferable by re-reading the policy file.

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

