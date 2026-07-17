# Durable Checkpoints

A checkpoint is the minimum committed state another engineer or agent needs to resume
an unfinished task without reconstructing the run transcript. It records progress and
decisions, not conversation history.

## Canonical discovery

Durable task state lives at:

```text
workflows/active/<task-id>/task.yaml
workflows/active/<task-id>/checkpoint.yaml
```

The checkpoint stores the repository-relative task path, objective, optional run ID,
completed work, decisions, remaining work, blockers, artifact references, and the last
portable event sequence when known. Absolute paths and secrets are forbidden.

Task IDs are restricted to safe alphanumeric dot/dash/underscore identifiers so
discovery cannot escape `workflows/active`.

## Create a handoff

The task specification must already be inside the repository:

```bash
esc-exec checkpoint create ampm-backend \
  workflows/active/task-123/task.yaml \
  --run-id run-123 \
  --status blocked \
  --completed "Reproduced the dependency violation." \
  --decision "Keep the existing public API." \
  --remaining "Move the adapter behind the content boundary." \
  --blocker "Needs confirmation of portal ownership." \
  --artifact .execution/runs/run-123/architecture.json
```

Creation never overwrites an existing checkpoint. Review and commit the generated
`checkpoint.yaml`; the tool prints this reminder explicitly.

## Update and resume

Updates append unique entries and preserve prior decisions:

```bash
esc-exec checkpoint update ampm-backend task-123 \
  --status ready-to-resume \
  --clear-blockers \
  --completed "Confirmed portal ownership." \
  --remaining "Run the content component gate."

esc-exec checkpoint inspect ampm-backend task-123
```

`inspect` emits compact JSON for AI consumption. The YAML file remains the canonical
committed representation. Invalid updates are rolled back, so a blocked checkpoint
cannot lose all blockers and a ready-to-resume checkpoint cannot lose all remaining
work.

Each progress list is limited to 50 entries and each entry to 1,000 characters. Link
to complete reports through relative artifact paths rather than embedding them.

## Runtime failure candidates

The orchestrator writes a transient `checkpoint.yaml` candidate when a run fails and
exposes it at `GET /runs/{id}/checkpoint`. This preserves the blocker even if the
runtime failed before producing its normal artifacts.

Candidates are not written into or committed to the consuming repository
automatically. Review the candidate, promote meaningful state with `checkpoint create`
or `checkpoint update`, and commit it. This keeps transient infrastructure failures
from polluting project history while making genuine incomplete work resumable.

## Completion

When the task is complete, archive its workflow according to the repository's workflow
convention. Do not leave a ready-to-resume checkpoint under `workflows/active` for a
completed task.
