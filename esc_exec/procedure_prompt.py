"""Prompt lines every adapter adds for the task's procedure (shared: the three adapters build their own prompts).

What the agent is told must match what the runtime enforces: a read-only task is told not to modify anything *and*
runs under a policy and a post-run check that make that true; a `document` task is told to cite only what exists
and is checked for it (esc_exec.grounding).
"""
from __future__ import annotations

from typing import Any

from esc_exec.root_cause import root_cause_prompt_lines

WORK_TYPE_LINES: dict[str, str] = {
    "plan": (
        "This is a planning task, not an implementation. Produce an implementation plan: the ordered steps, the "
        "files and components each touches, the risks, and how each step would be verified. Do not modify any file."
    ),
    "investigation": (
        "This is an investigation, not a change. Analyse the question and report your findings with evidence "
        "(file paths and line numbers, observed behaviour). Say what you could not determine. Do not modify any file."
    ),
    "document": (
        "This is a documentation task. Write documentation grounded in the code as it is now: describe only files, "
        "components and behaviour that exist in this repository, and cite files by their repository-relative path in "
        "backticks. Change documentation files only; do not touch source, tests or build files."
    ),
    "refactor": (
        "This is a refactor: change structure, not behaviour. Existing tests must keep passing unchanged -- do not "
        "edit a test to make it pass, and do not add features or fix bugs on the side."
    ),
}


def procedure_prompt_lines(context: dict[str, Any]) -> list[str]:
    """Work-type guidance, then the recorded root cause (for a fix). Empty for a task with neither."""
    work_type = context.get("task", {}).get("work_type")
    lines = [WORK_TYPE_LINES[work_type]] if work_type in WORK_TYPE_LINES else []
    return lines + root_cause_prompt_lines(context)
