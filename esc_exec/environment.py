from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any


DEFAULT_TCP_TIMEOUT_SECONDS = 2.0


def _describe(prerequisite: dict[str, Any]) -> str:
    kind = prerequisite.get("kind")
    if kind == "env":
        return f"env {prerequisite.get('name')}"
    if kind == "tcp":
        return f"tcp {prerequisite.get('host')}:{prerequisite.get('port')}"
    if kind == "file":
        return f"file {prerequisite.get('path')}"
    return f"{kind} {prerequisite}"


def _unsatisfied_reason(prerequisite: dict[str, Any], workspace_root: Path, timeout: float) -> str | None:
    """Returns a short reason the prerequisite is currently unsatisfied, or None if
    it's fine. Never raises -- an unreachable/misconfigured prerequisite is exactly
    the expected, reportable outcome, not a bug in the checker."""
    kind = prerequisite.get("kind")
    if kind == "env":
        return None if os.environ.get(prerequisite["name"]) else "not set"
    if kind == "tcp":
        try:
            with socket.create_connection((prerequisite["host"], prerequisite["port"]), timeout=timeout):
                return None
        except OSError as exc:
            return str(exc)
    if kind == "file":
        raw_path = Path(prerequisite["path"])
        path = raw_path if raw_path.is_absolute() else workspace_root / raw_path
        return None if path.is_file() else "not found"
    return f"unknown prerequisite kind {kind!r}"


def check_prerequisites(
    plan: dict[str, Any], workspace_root: Path, timeout: float = DEFAULT_TCP_TIMEOUT_SECONDS,
) -> list[str]:
    """
    Resolves every `ready`-gate check's declared `prerequisites` (env/tcp/file)
    against the real local environment -- no agent dispatch, no gate command
    execution. Mirrors `execute_verification_plan`'s own gate-skip semantics: a gate
    that isn't `ready` (not-applicable, input-required) never runs its command for
    real, so its prerequisites aren't checked either. Returns one human-readable
    blocker string per unsatisfied prerequisite, same shape as any other blocker
    list this codebase already threads through `checkpoint.yaml`.
    """
    blockers = []
    for gate in plan["gates"]:
        if gate["status"] != "ready":
            continue
        for check in gate.get("checks", []):
            for prerequisite in check.get("prerequisites", []) or []:
                reason = _unsatisfied_reason(prerequisite, workspace_root, timeout)
                if reason is not None:
                    blockers.append(
                        f"gate {gate['id']}, check {check['id']}: prerequisite "
                        f"{_describe(prerequisite)} unreachable ({reason})"
                    )
    return blockers
