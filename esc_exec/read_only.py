"""Read-only work: the `plan` and `investigation` work types must not change the repository.

Their procedures (esc_exec.procedures) simply have no `implement` stage, which on its own stops nothing -- an
agent granted edit permission would edit anyway. This module is the enforcement half:

- `effective_policy` forces the run's policy down so the agent is not even *offered* edit, execute or network
  tools (execute is denied too, because a shell can write);
- `state_violations` compares repository snapshots taken before and after the run, independently of the adapter
  and the policy, so a mapping bug or an adapter that ignores permissions still fails closed.

The snapshot itself (git) lives in `esc_exec.worktree`; this module is pure. The check reports and fails the run;
it never reverts, because an automatic revert is itself a destructive edit to someone's checkout.
"""
from __future__ import annotations

import copy
from typing import Any

READ_ONLY_WORK_TYPES = frozenset({"plan", "investigation"})

# escape-ai's own bookkeeping directories; changes here are the tool working, not the agent editing.
IGNORED_PREFIXES = (".esc-ai/runs/", ".esc-ai/worktrees/")

_FORCED_DENY = ("edit", "execute", "network", "external_paths")


def is_read_only(work_type: str | None) -> bool:
    return work_type in READ_ONLY_WORK_TYPES


def effective_policy(policy_document: dict[str, Any], work_type: str | None) -> dict[str, Any]:
    """The policy a run is actually granted. Unchanged for work that may edit; for read-only work, `read` keeps
    whatever was configured and everything that could change state is `deny`. Never returns a document more
    permissive than the one passed in."""
    if not is_read_only(work_type):
        return policy_document
    forced = copy.deepcopy(policy_document)
    permissions = forced.setdefault("permissions", {})
    for category in _FORCED_DENY:
        permissions[category] = "deny"
    permissions.setdefault("read", "allow")
    forced.pop("approvals", None)
    return forced


def relevant(path: str) -> bool:
    return not path.startswith(IGNORED_PREFIXES)


def state_violations(before: dict[str, Any] | None, after: dict[str, Any] | None) -> list[str]:
    """Human-readable differences between two `esc_exec.worktree.repository_state` snapshots ([] when identical).

    A snapshot is `{"head": sha, "files": {path: content-hash-or-None}}` where `files` lists every path git
    reports as changed or untracked. None (not a git repository) cannot be compared and yields no violations;
    the caller records that the check was skipped rather than pretending it passed."""
    if before is None or after is None:
        return []
    violations: list[str] = []
    if before["head"] != after["head"]:
        violations.append(f"HEAD moved from {before['head'][:10]} to {after['head'][:10]} (a commit, checkout or reset)")
    before_files, after_files = before["files"], after["files"]
    for path in sorted(set(before_files) | set(after_files)):
        if not relevant(path):
            continue
        if path not in before_files:
            violations.append(f"{path}: created or newly modified")
        elif path not in after_files:
            violations.append(f"{path}: a previously changed file was restored or removed")
        elif before_files[path] != after_files[path]:
            violations.append(f"{path}: modified again")
    return violations


def changed_paths(before: dict[str, Any] | None, after: dict[str, Any] | None) -> list[str]:
    """Paths that are new or differ between two `esc_exec.worktree.repository_state` snapshots (escape-ai's own
    bookkeeping excluded). Used for a run that edited the live checkout, where there is no worktree branch to diff;
    work that was already uncommitted before the run is not counted. Empty when either snapshot is missing."""
    if before is None or after is None:
        return []
    before_files, after_files = before["files"], after["files"]
    return sorted(
        path for path in set(before_files) | set(after_files)
        if relevant(path) and before_files.get(path) != after_files.get(path)
    )
