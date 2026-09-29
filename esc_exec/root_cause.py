"""The `root_cause` stage of the `fix` procedure (esc_exec.procedures.ROOT_CAUSE).

A fix must record *why* the behaviour is wrong before a plan is produced, so the agent implements against a
stated cause instead of patching a symptom. This module is the gate: it checks that a root cause was captured
and is well-formed. It cannot check that the cause is *true* -- nothing observable decides that. Truth is what
the `verify` stage is for: the fix has to pass the real gates. So the gate deliberately rejects the ways a
root cause is captured in name only (missing, empty, no evidence, a restated symptom) and nothing more.

Pure: no I/O.
"""
from __future__ import annotations

import re
from typing import Any

ROOT_CAUSE_FIELDS = ("statement", "evidence", "reproduction")


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def validate_root_cause(value: Any, objective: str = "") -> dict[str, Any]:
    """Return the root cause in canonical form (`statement`, `evidence` list, optional `reproduction`).

    Raises ValueError, naming what is wrong, when it is not a usable root cause. `objective` is the problem
    statement the fix was requested with: a statement identical to it only restates the symptom.
    """
    if not isinstance(value, dict):
        raise ValueError("root_cause must be an object with `statement` and `evidence`")
    unknown = sorted(set(value) - set(ROOT_CAUSE_FIELDS))
    if unknown:
        raise ValueError(f"root_cause has unknown field(s): {', '.join(unknown)}")
    statement = value.get("statement")
    if not isinstance(statement, str) or not statement.strip():
        raise ValueError("root_cause.statement is required: say what is actually wrong, not just what is observed")
    statement = statement.strip()
    if objective and _normalize(statement) == _normalize(objective):
        raise ValueError(
            "root_cause.statement only restates the objective (the symptom); "
            "state the underlying cause, e.g. which code path or assumption is wrong"
        )
    evidence = value.get("evidence")
    if isinstance(evidence, str):
        evidence = [item.strip() for item in evidence.split(";")]
    if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
        raise ValueError("root_cause.evidence must be a list of strings")
    evidence = [item.strip() for item in evidence if item.strip()]
    if not evidence:
        raise ValueError(
            "root_cause.evidence is required: how was this established? "
            "(a reproduction, a log line, a `file:line`, a failing test)"
        )
    canonical: dict[str, Any] = {"statement": statement, "evidence": evidence}
    reproduction = value.get("reproduction")
    if reproduction is not None:
        if not isinstance(reproduction, str) or not reproduction.strip():
            raise ValueError("root_cause.reproduction, when given, must be a non-empty string (a command or steps)")
        canonical["reproduction"] = reproduction.strip()
    return canonical


def root_cause_prompt_lines(context: dict[str, Any]) -> list[str]:
    """Prompt lines that put a task's recorded root cause in front of the agent (shared by every adapter).

    A task without one (any work type but `fix`, or a task predating the field) adds nothing. For a `fix` the
    agent is told to fix this cause and to say so if the evidence turns out to be wrong, not to patch around it.
    """
    task = context.get("task", {})
    root_cause = task.get("root_cause")
    if not root_cause:
        return []
    lines = [f"Root cause (established before implementation): {root_cause['statement']}"]
    lines += [f"Evidence: {item}" for item in root_cause["evidence"]]
    if root_cause.get("reproduction"):
        lines.append(f"Reproduce with: {root_cause['reproduction']}")
    lines.append(
        "Fix this cause, not just the symptom, and change nothing unrelated. If the evidence turns out to be "
        "wrong, say so plainly in your result instead of working around it."
    )
    return lines
