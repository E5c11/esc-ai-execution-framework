from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from esc_exec.claude_client import ClaudeCodeClient, ClaudeCodeError
from esc_exec.planning import WORK_TYPES

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def extract_json_object(text: str) -> str:
    """
    Extract a JSON object from a model response that may include prose before or
    after it. Confirmed live against a real onboarding run: despite an explicit "no
    commentary" instruction, the model prefaced its answer with a full sentence
    ("This is an Android-only demo app..., so no `targets` to report.") before the
    fenced JSON block -- a naive "does the text start with ```" check (the previous
    version of this function) silently failed to parse anything at all in that case,
    dropping every suggestion, not just the field the commentary was about.

    Tries, in order: a fenced code block found anywhere in the text; the substring
    between the first "{" and the last "}"; the raw text itself. Returns the best
    candidate substring -- callers still need to json.loads it and handle failure,
    this doesn't guarantee valid JSON.
    """
    fence_match = _JSON_FENCE_RE.search(text)
    if fence_match:
        return fence_match.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return text.strip()


class GroundableField:
    """
    One entry in `GROUNDABLE_FIELDS` -- see
    plan/active/generic-multi-component-detection.md design section 5. A
    "groundable field" is a per-component onboarding question genuinely
    answerable by reading the repository's real source, as opposed to an open
    product/judgment call (those never get an AI-suggest path -- see that
    plan's Non-goals). Adding a new groundable field means adding an entry
    here, not a new bespoke function or a new AI call.
    """

    def __init__(self, key: str, needed_line: str, guidance: str, extract):
        self.key = key  # applicability dict key, e.g. "purpose"
        self.needed_line = needed_line  # "Components needing a `purpose` description"
        self.guidance = guidance  # how to answer, included in the prompt once
        self.extract = extract  # (entry: dict) -> dict of validated answer keys


def _extract_purpose(entry: dict[str, Any]) -> dict[str, Any]:
    purpose = entry.get("purpose")
    return {"purpose": purpose.strip()} if isinstance(purpose, str) and purpose.strip() else {}


def _extract_frameworks_targets(entry: dict[str, Any]) -> dict[str, Any]:
    answer: dict[str, Any] = {}
    frameworks = entry.get("frameworks")
    if isinstance(frameworks, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in frameworks.items()):
        answer["frameworks"] = frameworks
    targets = entry.get("targets")
    if isinstance(targets, list) and all(isinstance(t, str) for t in targets):
        answer["targets"] = targets
    return answer


def _extract_architecture_style(entry: dict[str, Any]) -> dict[str, Any]:
    style = entry.get("architecture_style")
    if style in ("web-app", "web-content"):
        return {"architecture_style": style}
    return {}


GROUNDABLE_FIELDS: list[GroundableField] = [
    GroundableField(
        key="purpose",
        needed_line="Components needing a `purpose` description",
        guidance=(
            "purpose: ONE concise sentence describing what the component actually "
            'does, based on its real source -- matching this style: "Handles user '
            'authentication and session tokens" or "Owns lesson publishing."'
        ),
        extract=_extract_purpose,
    ),
    GroundableField(
        key="frameworks_targets",
        needed_line="Components needing `frameworks`/`targets`",
        guidance=(
            "frameworks: third-party libraries actually used, as field:value pairs "
            "(field = category such as network/database/di, value = the specific "
            "library, e.g. network:ktor, database:room, di:hilt). Only include ones "
            "you're genuinely confident about from real dependency declarations -- "
            "an empty object is a valid, confident answer if none are recognizable.\n"
            "targets: platform targets (e.g. ios) only if genuinely declared (e.g. a "
            "Kotlin Multiplatform target block) -- an empty list is a valid, "
            "confident answer otherwise."
        ),
        extract=_extract_frameworks_targets,
    ),
    GroundableField(
        key="architecture_style",
        needed_line="Components needing an `architecture_style` classification",
        guidance=(
            "architecture_style: read the component's real source (route handlers, "
            "Server Actions, page/data-fetching shape) and classify it as EXACTLY "
            'one of "web-app" (forms/mutations/Server-Actions-heavy -- the '
            'component writes data, not just displays it) or "web-content" '
            "(SSG/ISR/content-display-heavy -- the component mainly renders "
            "content and doesn't drive mutations). Only answer if you're genuinely "
            "confident from real source; omitting this key is a valid, confident "
            "response -- never guess."
        ),
        extract=_extract_architecture_style,
    ),
]


def suggest_onboarding_answers(
    client: ClaudeCodeClient, repository: Path,
    purpose_component_ids: list[str], frameworks_component_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """
    Tier 2 of plan/onboarding-answer-detection-and-suggestion.md: a single batched,
    read-only suggestion call covering every groundable field (`GROUNDABLE_FIELDS`)
    a component still needs an answer for. One call for everything, not one per
    field per component: a live smoke test this session measured ~40-50K tokens of
    pure fixed overhead per `claude -p` invocation, so batching is a real cost
    concern, not premature optimization. (Originally shipped as purpose-only under
    the name `suggest_purposes`, then extended to frameworks/targets, then
    refactored into the `GroundableField` registry above so a future groundable
    field is additive rather than a third hardcoded branch.)

    Returns {component_id: {...}} -- only the keys actually requested and actually
    answered are present per component; a component asked about but not confidently
    answerable is simply absent from its own sub-dict, not filled with an invented
    value.

    Fails open, never raises: any subprocess/parsing/schema failure returns {} so
    callers fall back to asking the plain question, exactly as if this were never
    called -- a wrong or missing suggestion is never worse than the question that
    already existed.
    """
    return _suggest_groundable_answers(
        client, repository,
        applicability={"purpose": set(purpose_component_ids), "frameworks_targets": set(frameworks_component_ids)},
    )


def groundable_component_ids(applicability: dict[str, set[str]]) -> list[str]:
    return sorted(set().union(*applicability.values())) if applicability else []


def build_groundable_prompt(applicability: dict[str, set[str]]) -> tuple[str, list[GroundableField]]:
    """
    Pure prompt-building half of the groundable-fields engine -- shared between
    the one-shot path (`suggest_onboarding_answers`, below, via `client.ask()`)
    and the session-based path (`esc_exec.conversation.suggest_groundable_answers_
    turn`, via `run_turn`/`--resume`) so a resumed second turn for purpose/
    frameworks (plan/active/generic-multi-component-detection.md design section 5)
    doesn't duplicate this logic. `client.ask()` has no `--resume` support at all
    (see its docstring: the lightweight, always-fresh path) -- only `client.run()`/
    `run_turn` does, which is why the session-based caller has to live in
    `conversation.py` rather than here, despite asking the exact same question.

    Returns (prompt, fields) -- `fields` is just `applicability`'s keys resolved
    back to their `GroundableField` objects, handed back so the caller doesn't
    need to re-derive it before calling `parse_groundable_response`.
    """
    fields = [field for field in GROUNDABLE_FIELDS if applicability.get(field.key)]
    prompt_lines = [
        "You are onboarding a software repository. For each component below, explore "
        "its real source directory (the repository's build file, e.g. "
        "settings.gradle.kts, declares each component as a subproject -- find its "
        "real directory yourself rather than guessing from the name alone) and "
        "provide the specific information requested for it.",
        "",
    ]
    prompt_lines += [
        f"{field.needed_line}: {', '.join(sorted(applicability.get(field.key, ()))) or '(none)'}"
        for field in fields
    ]
    prompt_lines += ["", *[field.guidance for field in fields]]
    prompt_lines += [
        "",
        "Respond with ONLY a JSON object, no markdown fences, no commentary, nothing "
        "else -- omit any key for a field a component wasn't asked about, and omit "
        "any key you're not actually confident about (never invent a "
        "plausible-sounding answer). Example:",
        '{"core-api": {"purpose": "...", "frameworks": {"network": "ktor"}, "targets": []}, '
        '"feature": {"purpose": "..."}}',
    ]
    return "\n".join(prompt_lines), fields


def parse_groundable_response(
    result_text: str, applicability: dict[str, set[str]], fields: list[GroundableField],
) -> dict[str, dict[str, Any]]:
    """Pure response-parsing half -- see build_groundable_prompt. `applicability`
    must be the same per-field component-ID mapping the prompt was built from --
    a component only gets a field's keys extracted if it was genuinely asked about
    that specific field, never every component that was asked about anything.
    Never raises; an unparseable or malformed response yields {}, same fail-open
    discipline as every other AI-suggestion path in this codebase."""
    try:
        parsed = json.loads(extract_json_object(result_text))
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}

    component_ids = groundable_component_ids(applicability)
    suggestions: dict[str, dict[str, Any]] = {}
    for component_id, entry in parsed.items():
        if component_id not in component_ids or not isinstance(entry, dict):
            continue
        answer: dict[str, Any] = {}
        for field in fields:
            if component_id in applicability.get(field.key, ()):
                answer.update(field.extract(entry))
        if answer:
            suggestions[component_id] = answer
    return suggestions


def _suggest_groundable_answers(
    client: ClaudeCodeClient, repository: Path, applicability: dict[str, set[str]],
) -> dict[str, dict[str, Any]]:
    """
    One-shot engine behind suggest_onboarding_answers: given which component IDs
    need an answer for which `GROUNDABLE_FIELDS` key, build one batched prompt
    covering exactly those fields, run it via the lightweight `client.ask()` path,
    and extract only the keys each component actually asked about.
    """
    component_ids = groundable_component_ids(applicability)
    if not component_ids:
        return {}
    prompt, fields = build_groundable_prompt(applicability)
    try:
        outcome = client.ask(repository, prompt, ["Read", "Glob", "Grep"])
    except ClaudeCodeError:
        return {}
    if outcome.get("is_error"):
        return {}
    result_text = outcome.get("result")
    if not isinstance(result_text, str):
        return {}
    return parse_groundable_response(result_text, applicability, fields)


def suggest_work_type_drift(
    client: ClaudeCodeClient, repository: Path, work_type: str, objective: str,
    scope_boundary: str, completion_conditions: list[str],
) -> dict[str, Any]:
    """
    plan/active/planning-consistency-checks.md design section 1: checks whether a
    plan's declared work_type still fits what's actually being described, once
    objective/scope_boundary/completion_conditions are known -- regardless of
    whether they came from today's static question path or a future conversation
    path. Text-only judgment, no repository file access needed (unlike
    onboarding's suggestions, which ground themselves in real source) -- granted
    zero tools.

    Never silently reclassifies or blocks (explicit design decision) -- this only
    reports a suggestion; the caller is responsible for asking the human to
    confirm the new type or explicitly keep the original.

    Returns {"drifted": bool, "suggested_work_type": str | None, "reasoning": str
    | None}. `suggested_work_type` is only ever one of the real WORK_TYPES values
    (never invented, never equal to the declared type), and is always
    accompanied by a non-empty `reasoning` string when `drifted` is True -- a
    drift claim with no grounding is worse than no check at all. Fails open,
    never raises: any subprocess/parsing/schema failure, or a self-contradictory
    response, returns drifted=False -- a failed check must never block or
    distort planning, same discipline as every other AI-suggestion path in this
    codebase.
    """
    no_drift = {"drifted": False, "suggested_work_type": None, "reasoning": None}
    completion_lines = [f"  - {condition}" for condition in completion_conditions] or ["  (none stated)"]
    prompt = "\n".join([
        "A software change was planned with the following declared work_type and "
        "description. Decide whether the declared work_type still genuinely fits "
        "the description, or whether the described work has grown into (or "
        "always really was) a different one.",
        "",
        f"Declared work_type: {work_type}",
        f"Objective: {objective}",
        f"Scope boundary (explicitly out of scope): {scope_boundary or '(none stated)'}",
        "Completion conditions:",
        *completion_lines,
        "",
        f"Valid work_type values: {', '.join(WORK_TYPES)}.",
        "Only report drift if you are genuinely confident the declared type no "
        "longer fits -- e.g. a declared `fix` that actually describes new "
        "behavior, not a correction, or a declared `feature` that's really just "
        "a `refactor` with no new behavior. When in doubt, do not report drift.",
        "",
        "Respond with ONLY a JSON object, no markdown fences, no commentary, "
        'nothing else: {"drifted": true|false, "suggested_work_type": "<one of '
        'the valid values>"|null, "reasoning": "<one sentence, only if '
        'drifted>"|null}. suggested_work_type and reasoning must both be null '
        "when drifted is false. Never suggest a value outside the valid list, "
        "and never suggest the same value that was already declared.",
    ])
    try:
        outcome = client.ask(repository, prompt, [])
    except ClaudeCodeError:
        return no_drift
    if outcome.get("is_error"):
        return no_drift
    result_text = outcome.get("result")
    if not isinstance(result_text, str):
        return no_drift
    try:
        parsed = json.loads(extract_json_object(result_text))
    except json.JSONDecodeError:
        return no_drift
    if not isinstance(parsed, dict) or parsed.get("drifted") is not True:
        return no_drift
    suggested = parsed.get("suggested_work_type")
    reasoning = parsed.get("reasoning")
    if (
        isinstance(suggested, str) and suggested in WORK_TYPES and suggested != work_type
        and isinstance(reasoning, str) and reasoning.strip()
    ):
        return {"drifted": True, "suggested_work_type": suggested, "reasoning": reasoning.strip()}
    return no_drift


def suggest_architecture_coverage_gap(
    client: ClaudeCodeClient, framework_root: Path, objective: str, resolved_documents: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    plan/active/planning-consistency-checks.md design section 2: checks whether a
    component's already-resolved architecture.profile_ids (resolved once,
    generically, at onboarding time from a static frameworks/targets lookup, not
    from this objective) actually give real guidance for this specific objective.

    Unlike suggest_work_type_drift, this grants Read/Glob/Grep scoped to the
    architecture framework's own checkout (framework_root) -- judging real
    documentation coverage benefits from reading actual document content, not just
    the index's id/tags/layer metadata alone.

    No resolved documents at all is treated as an uncovered gap without spending an
    AI call on it -- there is nothing to judge coverage against.

    Returns {"covered": bool, "reasoning": str | None, "suggested_title": str |
    None}. `covered=False` is only ever returned with both a non-empty `reasoning`
    and a non-empty `suggested_title` (a starting point for a local architecture
    note's title, never the note itself -- drafting one is a separate, explicit
    step) -- an uncovered claim with no grounding is worse than no check at all.
    Fails open to covered=True on any subprocess/parsing/schema failure -- a
    broken check must never manufacture a false "not covered" warning, same
    discipline as suggest_work_type_drift.
    """
    covered = {"covered": True, "reasoning": None, "suggested_title": None}
    if not resolved_documents:
        return {
            "covered": False,
            "reasoning": "No architecture framework documents are resolved for this component at all.",
            "suggested_title": None,
        }
    doc_lines = [
        f"- {document['id']} ({document.get('path', '?')}): tags={', '.join(document.get('tags') or [])}"
        for document in resolved_documents
    ]
    prompt = "\n".join([
        "A software change is being planned for a component whose architecture "
        "framework already has these documents resolved for it -- read any of "
        "them yourself if you need to, they're real files under this directory:",
        "",
        *doc_lines,
        "",
        f"Objective: {objective}",
        "",
        "Judge whether these documents actually give real, relevant guidance for "
        "implementing this specific objective -- not just whether they were "
        "resolved for this component in general (they were, generically, based on "
        "the component's tech stack, not based on this objective). Only report a "
        "gap if you're genuinely confident none of these documents meaningfully "
        "cover this objective's concern.",
        "",
        "Respond with ONLY a JSON object, no markdown fences, no commentary, "
        'nothing else: {"covered": true|false, "reasoning": "<one sentence, only '
        'if not covered>"|null, "suggested_title": "<a short title for a new '
        'architecture note covering this gap, only if not covered>"|null}.',
    ])
    try:
        outcome = client.ask(framework_root, prompt, ["Read", "Glob", "Grep"])
    except ClaudeCodeError:
        return covered
    if outcome.get("is_error"):
        return covered
    result_text = outcome.get("result")
    if not isinstance(result_text, str):
        return covered
    try:
        parsed = json.loads(extract_json_object(result_text))
    except json.JSONDecodeError:
        return covered
    if not isinstance(parsed, dict) or parsed.get("covered") is not False:
        return covered
    reasoning = parsed.get("reasoning")
    title = parsed.get("suggested_title")
    if isinstance(reasoning, str) and reasoning.strip() and isinstance(title, str) and title.strip():
        return {"covered": False, "reasoning": reasoning.strip(), "suggested_title": title.strip()}
    return covered

