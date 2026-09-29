"""The `baseline_capture` stage of the `refactor` procedure (esc_exec.procedures.BASELINE_CAPTURE).

A refactor claims behaviour did not change. The only evidence available is the project's own checks, so before the
agent touches anything they are run against the untouched code, and the run is refused unless that baseline is
meaningful. Two facts about `esc_exec.verification_execution` make this necessary rather than decorative:

- a plan whose gates are all skipped or empty is reported `passed` -- verification can be vacuously green, and a
  refactor with no tests would "verify" having proved nothing;
- a check that already fails before the change cannot show the change preserved anything.

After the agent runs, the ordinary `verify` gate decides. The plan is built once from the live checkout, so the
same checks run before and after; "verify passed" therefore already means every baseline check passed again, and
no separate diff is needed. The baseline result is kept with the run as the record of what "unchanged" meant.

Pure: no I/O. The gateway that runs the plan is `esc_exec.verification_execution`.
"""
from __future__ import annotations

from typing import Any


def _checks(result: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(gate["id"], check) for gate in result["gates"] for check in gate["checks"]]


def plan_blockers(plan: dict[str, Any]) -> list[str]:
    """Reasons a verification plan cannot back a refactor, judged before anything is run: no gate is `ready` with a
    check in it (gates awaiting input, or with no checks, are skipped, so nothing would run)."""
    runnable = [
        check["id"] for gate in plan["gates"] if gate["status"] == "ready" for check in gate.get("checks", [])
    ]
    if runnable:
        return []
    return [
        "no verification check can run for these components, so nothing could show a refactor preserved behaviour; "
        "declare a runnable check in the component's verification profile first"
    ]


def baseline_blockers(result: dict[str, Any]) -> list[str]:
    """Reasons a baseline run cannot back a refactor: any check that did not pass, or no check that did."""
    blockers = [
        f"baseline check {gate_id}.{check['id']} is {check['status']} before any change; a refactor cannot be "
        "judged against a broken baseline (fix that first, e.g. with `escape-ai fix`)"
        for gate_id, check in _checks(result)
        if check["status"] in {"failed", "error"}
    ]
    if not blockers and not any(check["status"] == "passed" for _, check in _checks(result)):
        blockers.append("no verification check actually ran against the untouched code, so there is no baseline")
    return blockers


def passed_check_count(result: dict[str, Any]) -> int:
    return sum(1 for _, check in _checks(result) if check["status"] == "passed")
