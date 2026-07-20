from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from esc_exec.claude_code_adapter import (
    ClaudeCodeClient, ClaudeCodeError, build_groundable_prompt, extract_json_object,
    groundable_component_ids, parse_groundable_response, result_message,
)
from esc_exec.planning import WORK_TYPES
from esc_exec.roadmap import save_conversation_summary

# Real, API-reported context-window consumption thresholds only -- no fabricated
# "response quality/degradation" signal (see plan/ai-conversation-primitive.md's
# explicit non-goal: there is no API-exposed quality metric to trigger on). The hard
# threshold matches the exact 90% precedent already used in
# task-orchestration-and-verification-loop.md for subscription-usage dispatch
# pausing -- same reasoning, same number.
SOFT_CONTEXT_THRESHOLD = 0.65
HARD_CONTEXT_THRESHOLD = 0.90


def context_window_from_usage(outcome: dict[str, Any]) -> int | None:
    """
    The primary model's real context window, from a `result` message's `modelUsage`
    breakdown. Verified live 2026-07-19/20 against claude-code 2.1.215 -- a real
    response included:
    {"claude-sonnet-5": {..., "contextWindow": 1000000},
     "claude-haiku-4-5-20251001": {..., "contextWindow": 200000}}
    Takes the max across reported models, since a cheap routing/haiku model can
    appear alongside the primary model with a smaller window -- the conversation's
    real budget is the larger one. Returns None if `modelUsage` is missing or has no
    usable `contextWindow` field -- callers must treat that as unmeasurable, not as
    zero consumption.
    """
    model_usage = outcome.get("modelUsage")
    if not isinstance(model_usage, dict) or not model_usage:
        return None
    windows = [
        entry.get("contextWindow") for entry in model_usage.values()
        if isinstance(entry, dict) and isinstance(entry.get("contextWindow"), int) and entry.get("contextWindow") > 0
    ]
    return max(windows) if windows else None


def context_consumption_ratio(outcome: dict[str, Any]) -> float | None:
    """
    Fraction of the model's real context window consumed by this turn's total input
    (cache_read + cache_creation + input tokens). With `--resume`, this is the whole
    accumulated conversation's context sent for this turn, not just what's new --
    verified live: a resumed turn's `cache_read_input_tokens` reflects everything
    cached from prior turns, so this ratio genuinely tracks "how full is this
    conversation" turn over turn, not just this turn's fresh cost.

    Returns None if it can't be computed (missing usage/modelUsage fields) --
    threshold_crossed treats that as "keep going," never as an implicit crossing.
    """
    context_window = context_window_from_usage(outcome)
    if not context_window:
        return None
    usage = outcome.get("usage") or {}
    consumed = (
        (usage.get("input_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0)
        + (usage.get("cache_read_input_tokens") or 0)
    )
    return consumed / context_window


def threshold_crossed(ratio: float | None) -> str | None:
    """
    "hard", "soft", or None. A None ratio (unmeasurable) never counts as a crossing
    -- fails open toward "keep the conversation going" rather than silently forcing
    a stop on missing data.
    """
    if ratio is None:
        return None
    if ratio >= HARD_CONTEXT_THRESHOLD:
        return "hard"
    if ratio >= SOFT_CONTEXT_THRESHOLD:
        return "soft"
    return None


def run_turn(
    client: ClaudeCodeClient, repository: Path, prompt: str, tools: list[str],
    resume_session_id: str | None = None, model: str | None = None,
) -> dict[str, Any]:
    """
    Runs exactly one turn of a multi-turn conversation. Uses the full stream-json
    path (`ClaudeCodeClient.run`) rather than the lightweight `ask()` path, since
    per-tool events matter here too -- a conversation can read real source files as
    part of forming an answer, the same as task execution does.

    Fails open on any `ClaudeCodeError` (subprocess failure, unparseable output) --
    returns an error result rather than raising, so an interactive loop driving many
    of these can decide what to do (retry, tell the human, bail out) instead of
    crashing the whole conversation outright.

    Returns: {"text": str | None, "session_id": str | None, "is_error": bool,
    "error_detail": str | None, "context_ratio": float | None,
    "threshold": "hard" | "soft" | None}

    An empty (but not erroring) `result` text is not itself treated as an error here
    -- unlike a bounded task run (ClaudeCodeAdapter.execute), a conversational turn
    producing no text this round is a plausible outcome (e.g. a tool-only turn), not
    necessarily a failure; the caller decides how to handle it.
    """
    try:
        messages = client.run(repository, prompt, tools, model, resume_session_id)
    except ClaudeCodeError as exc:
        return {
            "text": None, "session_id": resume_session_id, "is_error": True,
            "error_detail": str(exc), "context_ratio": None, "threshold": None,
        }
    outcome = result_message(messages)
    if outcome is None:
        return {
            "text": None, "session_id": resume_session_id, "is_error": True,
            "error_detail": "claude -p stream produced no terminal `result` message",
            "context_ratio": None, "threshold": None,
        }
    session_id = outcome.get("session_id", resume_session_id)
    if outcome.get("is_error"):
        return {
            "text": None, "session_id": session_id, "is_error": True,
            "error_detail": str(outcome.get("result"))[:500],
            "context_ratio": None, "threshold": None,
        }
    ratio = context_consumption_ratio(outcome)
    return {
        "text": outcome.get("result") or "", "session_id": session_id, "is_error": False,
        "error_detail": None, "context_ratio": ratio, "threshold": threshold_crossed(ratio),
    }


def _empty_progress() -> dict[str, list[str]]:
    return {"completed": [], "decisions": [], "remaining": [], "open_questions": []}


def _roadmap_context_text(existing_roadmap: dict[str, Any] | None) -> str:
    if not existing_roadmap:
        return ""
    roadmap = existing_roadmap.get("project_roadmap", {})
    return (
        "Existing roadmap for this repository (update it, don't ignore or restate it "
        "verbatim):\n"
        f"purpose: {roadmap.get('purpose')}\n"
        f"current_stage: {roadmap.get('current_stage')}\n"
        f"direction: {roadmap.get('direction')}\n"
        f"durable_decisions: {roadmap.get('durable_decisions')}\n\n"
    )


def compact_conversation(
    client: ClaudeCodeClient, repository: Path, conversation_id: str, session_id: str,
    purpose: str, existing_roadmap: dict[str, Any] | None = None, status: str = "in-progress",
) -> dict[str, Any]:
    """
    Sends one more turn to the *existing* session (`--resume`), asking the model to
    compact everything discussed so far -- the model already has the full
    conversation in context via session continuity, so no separate transcript needs
    to be passed in here.

    Two outputs, two different trust levels (see plan/ai-conversation-primitive.md):
    - `progress` (conversation_summary's completed/decisions/remaining/open_questions)
      is saved immediately via save_conversation_summary -- ephemeral, ok to trust the
      model's own summarization judgment without a human review gate.
    - `roadmap_proposal` is returned, NOT saved -- durable repository-level state
      must go through an explicit human confirm step before overwriting
      project_roadmap.yaml, same discipline as every other write path in this system.
      A caller that gets None back should treat it as "nothing durable changed this
      round," not as an error.

    Fails open: any subprocess/parsing failure still writes an empty-progress
    conversation_summary (so there's a durable record that compaction was attempted
    and failed, not silence) and returns roadmap_proposal=None.
    """
    prompt = "\n".join([
        "This conversation needs to be compacted now. Based on everything discussed "
        "in this session, produce two things.",
        "",
        _roadmap_context_text(existing_roadmap) +
        "1. progress: a short factual record of *this conversation specifically* -- "
        "completed (what was resolved), decisions (choices made and why), remaining "
        "(what's still to do), open_questions (what's genuinely undecided).",
        "2. roadmap: ONLY the durable facts that should outlive this conversation -- "
        "purpose (what this project/repo is for), current_stage (where implementation "
        "actually stands right now), direction (where it's headed), durable_decisions "
        "(architecture/stack choices that remain valid indefinitely). Do not put this "
        "conversation's tactical back-and-forth here -- only what a completely fresh "
        "session, with none of this conversation's context, would need to know to "
        "pick up where this left off. Omit the \"roadmap\" key entirely if nothing "
        "durable actually changed this conversation.",
        "",
        "Respond with ONLY a JSON object, no markdown fences, no commentary, shaped "
        "exactly like this:",
        '{"progress": {"completed": [...], "decisions": [...], "remaining": [...], '
        '"open_questions": [...]}, '
        '"roadmap": {"purpose": "...", "current_stage": "...", "direction": "...", '
        '"durable_decisions": [...]}}',
    ])
    turn = run_turn(client, repository, prompt, tools=[], resume_session_id=session_id)

    progress = _empty_progress()
    roadmap_proposal: dict[str, Any] | None = None
    if not turn["is_error"] and turn["text"]:
        try:
            parsed = json.loads(extract_json_object(turn["text"]))
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            raw_progress = parsed.get("progress")
            if isinstance(raw_progress, dict):
                progress = {
                    field: [item for item in raw_progress.get(field, []) if isinstance(item, str)]
                    if isinstance(raw_progress.get(field), list) else []
                    for field in ("completed", "decisions", "remaining", "open_questions")
                }
            raw_roadmap = parsed.get("roadmap")
            if isinstance(raw_roadmap, dict) and any(
                isinstance(raw_roadmap.get(field), str) for field in ("purpose", "current_stage", "direction")
            ):
                durable_decisions = raw_roadmap.get("durable_decisions")
                roadmap_proposal = {
                    "purpose": raw_roadmap.get("purpose"),
                    "current_stage": raw_roadmap.get("current_stage"),
                    "direction": raw_roadmap.get("direction"),
                    "durable_decisions": (
                        [item for item in durable_decisions if isinstance(item, str)]
                        if isinstance(durable_decisions, list) else []
                    ),
                }

    save_conversation_summary(
        repository, conversation_id, purpose, status=status,
        completed=progress["completed"], decisions=progress["decisions"],
        remaining=progress["remaining"], open_questions=progress["open_questions"],
    )
    return {"progress": progress, "roadmap_proposal": roadmap_proposal, "session_id": turn["session_id"]}


def suggest_unresolved_components(
    client: ClaudeCodeClient, repository: Path, unresolved: list[str], resume_session_id: str | None = None,
) -> dict[str, Any]:
    """
    Generic Tier 2 AI fallback for `BuildSystemAdapter.unresolved()` -- see
    plan/active/generic-multi-component-detection.md design section 3. Deliberately
    adapter-agnostic in its phrasing: never mentions Gradle, npm, or any
    build-system-specific syntax, just "identifier -> directory," which is exactly
    what `unresolved()`'s contract already provides regardless of which adapter
    produced it.

    Runs one `run_turn` call (not a fresh one-shot `client.ask()`) so this can be
    the first turn of a session a caller later resumes cheaply for a second turn
    (e.g. purpose/frameworks suggestion for the confirmed component list) -- see
    this module's own live-measured near-zero cost on a resumed turn.

    Returns {"resolved": {identifier: relative_directory, ...}, "session_id": str |
    None} -- `resolved` only contains identifiers `unresolved` actually listed,
    each with a directory that genuinely exists on disk; anything the model wasn't
    confident about, or that doesn't resolve to a real path, is simply absent, not
    filled with an invented value. Fails open: any turn failure returns an empty
    `resolved` dict, never raises.
    """
    if not unresolved:
        return {"resolved": {}, "session_id": resume_session_id}
    prompt = "\n".join([
        "This repository declares these identifiers in its build configuration, "
        f"but they could not be resolved to a real on-disk directory: {', '.join(unresolved)}.",
        "",
        "Explore the repository and figure out which existing directory each one "
        "actually corresponds to.",
        "",
        "Respond with ONLY a JSON object, no markdown fences, no commentary: "
        '{"<identifier>": "<repository-root-relative directory>", ...} -- omit any '
        "you can't confidently resolve, never guess.",
    ])
    turn = run_turn(client, repository, prompt, tools=["Read", "Glob", "Grep"], resume_session_id=resume_session_id)

    resolved: dict[str, str] = {}
    if not turn["is_error"] and turn["text"]:
        try:
            parsed = json.loads(extract_json_object(turn["text"]))
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            unresolved_set = set(unresolved)
            for identifier, relative_path in parsed.items():
                if (
                    identifier in unresolved_set
                    and isinstance(relative_path, str) and relative_path.strip()
                    and (repository / relative_path).is_dir()
                ):
                    resolved[identifier] = relative_path
    return {"resolved": resolved, "session_id": turn["session_id"]}


def suggest_groundable_answers_turn(
    client: ClaudeCodeClient, repository: Path, applicability: dict[str, set[str]],
    resume_session_id: str | None = None,
) -> dict[str, Any]:
    """
    Session-based twin of `claude_code_adapter.suggest_onboarding_answers` --
    same groundable-fields question, same prompt (via `build_groundable_prompt`),
    but run as a `run_turn` so a caller that already has a session open (e.g.
    `suggest_unresolved_components`'s module-resolution turn) can `--resume` it
    for this second turn instead of paying a fresh `claude -p` invocation's full
    setup cost again -- see plan/active/generic-multi-component-detection.md
    design section 5's "no new live per-question call" requirement.

    Returns {"suggestions": {component_id: {...}}, "session_id": str | None}, same
    per-component shape `suggest_onboarding_answers` returns. Fails open: any turn
    failure returns an empty `suggestions` dict, never raises.
    """
    component_ids = groundable_component_ids(applicability)
    if not component_ids:
        return {"suggestions": {}, "session_id": resume_session_id}
    prompt, fields = build_groundable_prompt(applicability)
    turn = run_turn(client, repository, prompt, tools=["Read", "Glob", "Grep"], resume_session_id=resume_session_id)
    suggestions: dict[str, dict[str, Any]] = {}
    if not turn["is_error"] and turn["text"]:
        suggestions = parse_groundable_response(turn["text"], applicability, fields)
    return {"suggestions": suggestions, "session_id": turn["session_id"]}


# plan/active/form-driven-planning-conversation.md design section 2. A literal
# marker line, not just "the last JSON blob in the text" -- the reply itself may
# legitimately contain code fences or other text a naive scan could misparse as
# the trailer, so an unambiguous, unlikely-to-occur-naturally marker is used to
# split the two halves cleanly.
FORM_TRAILER_MARKER = "---FORM---"
FORM_FIELDS = ("work_type", "objective", "components", "scope_boundary", "completion_conditions", "rollout_needs")


def suggest_form_turn(
    client: ClaudeCodeClient, repository: Path, message: str, current_objective: str,
    suggested_components: list[str], resume_session_id: str | None = None,
) -> dict[str, Any]:
    """
    One turn of the form-driven planning conversation (plan/active/form-driven-
    planning-conversation.md design section 2). Each turn's prompt instructs the
    model to end its reply with a `FORM_TRAILER_MARKER` line, then a fenced JSON
    object reporting current known values for every `FORM_FIELDS` entry (null/
    omitted for anything not yet determined) -- a per-turn trailer, not a
    separate extraction call, so the caller can notice completion itself
    without doubling the turn count (see that plan's "no new live per-question
    call" discipline -- doubling calls would double however many `--resume`
    turns a conversation takes).

    `components`/`scope_boundary`/`objective` are never asked about blind:
    `suggested_components` (the repository's own deterministic routing
    suggestion, from `route_objective` -- computed by the caller, this
    function has no repository access of its own, tools=[]) and
    `current_objective` are fed in as context the model confirms or revises,
    matching this system's onboarding-side "AI is enrichment on top of a
    deterministic signal, never re-deriving it from scratch" discipline.

    Returns {"reply": str, "form": dict[str, Any], "session_id": str | None,
    "threshold": "hard" | "soft" | None}. `reply` is the human-visible portion
    only -- the trailer is never shown to the human. `form` only contains keys
    the model reported a genuinely valid, non-null value for *this turn*
    (validated against each field's real shape -- work_type against the real
    WORK_TYPES, completion_conditions/components must be non-empty lists of
    strings, etc.); the caller merges across turns. `threshold` is `run_turn`'s
    own real context-consumption signal, passed straight through -- the same
    soft/hard safety net `run_planning_conversation_interactive` already uses,
    so this conversation can't run unbounded either.
    Fails open on any turn failure, and on a missing or unparseable trailer --
    both return an empty form, never a crash, so a malformed trailer degrades
    to "nothing new confirmed this turn," not a broken conversation.
    """
    prompt = "\n".join([
        "You are having a conversation with a developer to help them plan a "
        "software change to a specific repository. Steer the conversation "
        "toward filling in the form below -- ask about whatever's still "
        "missing, don't re-ask about anything already filled in, and keep "
        "your replies conversational and brief.",
        "",
        "Form fields:",
        "- work_type: one of feature, fix, refactor, maintenance, investigation",
        "- objective: a clear, concise description of the change",
        "- components: which of this repository's components it touches",
        "- scope_boundary: what's explicitly out of scope for this change",
        "- completion_conditions: what must be true for this to be considered done (a list)",
        "- rollout_needs: any rollout/deployment needs (optional -- fine to leave unset)",
        "",
        f"Starting objective: {current_objective}",
        "Components this repository's own routing already suggests might be "
        f"relevant (confirm or override, don't ask blind): {', '.join(suggested_components) or '(none matched)'}",
        "",
        "If the conversation drifts into something that sounds out of scope "
        "given what's already been discussed, say so explicitly rather than "
        "silently going along with it.",
        "",
        "After your reply to the developer, on its own line write exactly "
        f"`{FORM_TRAILER_MARKER}`, then a fenced JSON object with the form's "
        "current full state (not just what changed this turn) -- use null for "
        "anything not yet determined, never invent a value:",
        '```json',
        '{"work_type": "..."|null, "objective": "..."|null, "components": [...]|null, '
        '"scope_boundary": "..."|null, "completion_conditions": [...]|null, '
        '"rollout_needs": "..."|null}',
        '```',
        "",
        f"Developer: {message}",
    ])
    turn = run_turn(client, repository, prompt, tools=[], resume_session_id=resume_session_id)
    if turn["is_error"] or not turn["text"]:
        return {"reply": "", "form": {}, "session_id": turn["session_id"], "threshold": None}

    text = turn["text"]
    marker_index = text.find(FORM_TRAILER_MARKER)
    if marker_index == -1:
        return {"reply": text.strip(), "form": {}, "session_id": turn["session_id"], "threshold": turn["threshold"]}
    reply = text[:marker_index].strip()
    trailer_text = text[marker_index + len(FORM_TRAILER_MARKER):]
    try:
        parsed = json.loads(extract_json_object(trailer_text))
    except json.JSONDecodeError:
        return {"reply": reply, "form": {}, "session_id": turn["session_id"], "threshold": turn["threshold"]}
    if not isinstance(parsed, dict):
        return {"reply": reply, "form": {}, "session_id": turn["session_id"], "threshold": turn["threshold"]}

    form: dict[str, Any] = {}
    if parsed.get("work_type") in WORK_TYPES:
        form["work_type"] = parsed["work_type"]
    objective = parsed.get("objective")
    if isinstance(objective, str) and objective.strip():
        form["objective"] = objective.strip()
    components = parsed.get("components")
    if isinstance(components, list) and components and all(isinstance(c, str) and c.strip() for c in components):
        form["components"] = components
    scope_boundary = parsed.get("scope_boundary")
    if isinstance(scope_boundary, str) and scope_boundary.strip():
        form["scope_boundary"] = scope_boundary.strip()
    completion_conditions = parsed.get("completion_conditions")
    if (
        isinstance(completion_conditions, list) and completion_conditions
        and all(isinstance(c, str) and c.strip() for c in completion_conditions)
    ):
        form["completion_conditions"] = completion_conditions
    rollout_needs = parsed.get("rollout_needs")
    if isinstance(rollout_needs, str) and rollout_needs.strip():
        form["rollout_needs"] = rollout_needs.strip()
    return {"reply": reply, "form": form, "session_id": turn["session_id"], "threshold": turn["threshold"]}
