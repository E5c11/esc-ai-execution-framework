from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# plan/active (BLA-48, esc-ai-orchestrator): the per-work-type procedure contract.
# See esc-ai-orchestrator's VISION.md ("the procedure contract") for the thesis this
# implements -- a work type is a fixed, ordered sequence of Stages, not a hint the
# CLI or an AI provider is free to reinterpret. Two properties are load-bearing and
# must not be undermined by callers:
#
#   1. A Stage's presence in a procedure never depends on who is running it or which
#      role/autonomy_preference they have (see esc-ai-orchestrator's BLA-49). Role
#      may change a `variable` stage's interaction mode -- ask vs. auto-resolve --
#      never whether the stage runs.
#   2. `verify` and `architecture_gate`, wherever they appear in a procedure, are
#      never skipped and never silently auto-passed.
#
# This module intentionally does not import anything from esc_orchestrator. Some
# stages below (`implement`, and the interactive ask/confirm wrapper around
# `architecture_gate`) are actually carried out by orchestrator code, because that's
# where the Store/Scheduler and terminal interaction live -- this module only
# declares the contract those callers are expected to honor, identifying the
# existing esc_exec primitive (or "new" for a stage with no primitive yet) each
# stage is built on.
#
# The work-type vocabulary here (fix, feature, plan, refactor, document, investigate,
# job) is the public, intent-based vocabulary from BLA-42 -- deliberately not the
# same strings as planning.WORK_TYPES. `investigate`/`fix`/`feature`/`refactor` here
# correspond 1:1 to WORK_TYPES's `investigation`/`fix`/`feature`/`refactor`; `job` is
# the new alias for WORK_TYPES's `maintenance`; `plan` and `document` are net new.
# Reconciling WORK_TYPES itself against this vocabulary is BLA-42's job when it wires
# the CLI, not this module's.

StageKind = Literal["gate", "question", "action"]
StageInteraction = Literal["fixed", "variable"]


@dataclass(frozen=True)
class Stage:
    """
    One step of a work type's procedure.

    mandatory is always True in v1 -- there is no lever yet for an optional stage,
    deliberately (see BLA-48's non-goals). The field exists so callers can rely on
    `stage.mandatory` rather than assuming presence-in-a-procedure implies it.

    interaction is "fixed" (never affected by role/autonomy_preference -- always
    runs the same way for everyone) or "variable" (role controls whether the human
    is asked or the system auto-resolves; the stage itself still always runs).

    maps_to documents the existing esc_exec primitive (or orchestrator function,
    prefixed accordingly) this stage is built on, or "new" if no primitive exists
    yet. It is a plain string, not an import -- binding a stage to a real callable
    is the calling CLI's job (BLA-42), not this contract's.
    """

    name: str
    kind: StageKind
    interaction: StageInteraction
    maps_to: str
    mandatory: bool = True


# Stage vocabulary shared across procedures. Each work type's procedure below
# reuses these exact instances rather than redeclaring equivalent ones, so a change
# to a stage's interaction mode or mapping is made once, not per work type.

ROUTE = Stage(
    name="route", kind="action", interaction="fixed",
    maps_to="esc_exec.planning.route_objective",
)
SCOPE_DECLARE = Stage(
    name="scope_declare", kind="question", interaction="fixed",
    maps_to="esc_exec.planning.planning_questions (components/depends_on fields)",
)
ARCHITECTURE_GATE = Stage(
    name="architecture_gate", kind="gate", interaction="variable",
    maps_to=(
        "esc_exec.claude_code_adapter.suggest_architecture_coverage_gap; a gap "
        "resolves against the already-established framework profile first. "
        "Anything uncovered is labeled `local` via esc_exec.local_architecture."
        "write_local_architecture_note; that note's auto-apply trust is governed "
        "by BLA-50's rating model, not by this stage's interaction mode alone."
    ),
)
OBJECTIVE_GATE = Stage(
    name="objective_gate", kind="question", interaction="variable",
    maps_to="esc_exec.planning.planning_questions (scope_boundary/completion_conditions/rollout_needs fields)",
)
ROOT_CAUSE = Stage(
    name="root_cause", kind="gate", interaction="fixed",
    maps_to=(
        "esc_exec.root_cause.validate_root_cause, enforced by esc_exec.planning (a fix cannot be planned without "
        "one) and esc_exec.contracts (a fix task is not executable without one); checks the cause was captured "
        "and is well-formed, not that it is true -- verify decides that; never auto-skippable"
    ),
)
BASELINE_CAPTURE = Stage(
    name="baseline_capture", kind="gate", interaction="fixed",
    maps_to=(
        "esc_exec.baseline (plan_blockers, baseline_blockers) over "
        "esc_exec.verification_execution.execute_verification_plan run against the untouched code before dispatch; "
        "refuses a refactor with no runnable check or a baseline that is not green, because verification can "
        "otherwise pass vacuously"
    ),
)
PLAN_PRODUCE = Stage(
    name="plan_produce", kind="action", interaction="fixed",
    maps_to="esc_exec.planning.generate_single_repository_workflow / generate_multi_repository_workflow",
)
IMPLEMENT = Stage(
    name="implement", kind="action", interaction="fixed",
    maps_to="esc_orchestrator.escape_ai_cli.execute_task (Store/Scheduler/adapter dispatch; not in esc_exec)",
)
VERIFY = Stage(
    name="verify", kind="gate", interaction="fixed",
    maps_to="esc_exec.verification_execution.execute_verification_plan; real subprocess results decide pass/fail, never the agent's own claim",
)
GROUNDING_CHECK = Stage(
    name="grounding_check", kind="gate", interaction="fixed",
    maps_to="new -- cross-checks generated documentation claims against the repository index/manifests before report",
)
REPORT = Stage(
    name="report", kind="action", interaction="variable",
    maps_to="esc_orchestrator.escape_ai_cli.render_execution_result and friends; interaction here means explanation depth, never correctness",
)


# A read-only procedure never includes `implement` -- there is no edit permission to
# deny, because the stage structurally does not exist for these work types. Shared
# by `investigate` and `plan`, which differ only in what `report` produces (findings
# vs. a plan document), not in the gates they run.
_READ_ONLY_PROCEDURE: tuple[Stage, ...] = (ROUTE, SCOPE_DECLARE, ARCHITECTURE_GATE, REPORT)

# Shared by `feature` and `job`: `job` differs only in how loosely the objective is
# stated going in, never in which stages run -- see the module docstring.
_FULL_CHANGE_PROCEDURE: tuple[Stage, ...] = (
    ROUTE, SCOPE_DECLARE, ARCHITECTURE_GATE, OBJECTIVE_GATE, PLAN_PRODUCE, IMPLEMENT, VERIFY, REPORT,
)

PROCEDURES: dict[str, tuple[Stage, ...]] = {
    "investigate": _READ_ONLY_PROCEDURE,
    "plan": _READ_ONLY_PROCEDURE,
    "fix": (
        ROUTE, SCOPE_DECLARE, ARCHITECTURE_GATE, ROOT_CAUSE, OBJECTIVE_GATE,
        PLAN_PRODUCE, IMPLEMENT, VERIFY, REPORT,
    ),
    "refactor": (
        ROUTE, SCOPE_DECLARE, ARCHITECTURE_GATE, BASELINE_CAPTURE, OBJECTIVE_GATE,
        PLAN_PRODUCE, IMPLEMENT, VERIFY, REPORT,
    ),
    "feature": _FULL_CHANGE_PROCEDURE,
    "job": _FULL_CHANGE_PROCEDURE,
    "document": (ROUTE, SCOPE_DECLARE, IMPLEMENT, GROUNDING_CHECK, VERIFY, REPORT),
}

PROCEDURE_WORK_TYPES = tuple(PROCEDURES.keys())


def procedure_for(work_type: str) -> tuple[Stage, ...]:
    if work_type not in PROCEDURES:
        raise ValueError(f"work_type must be one of: {', '.join(PROCEDURE_WORK_TYPES)}")
    return PROCEDURES[work_type]
