from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from esc_exec.contracts import validate_contract
from esc_exec.instructions import build_instruction_bundle
from esc_exec.json_io import write_json
from esc_exec.model import ManifestState
from esc_exec.planning import WORK_TYPES
from esc_exec.registry import resolve_route
from esc_exec.task_context import build_task_context
from esc_exec.yaml_io import load_yaml
from esc_exec.measurement import run_metrics


def claude_cli_available(binary: str = "claude") -> bool:
    """
    Just a PATH check -- not an auth check (see claude_auth_status for that). Enough
    to stop the subscription route from being offered as if it works when the CLI
    isn't even installed, without pretending to verify auth state too.
    """
    return shutil.which(binary) is not None


def claude_auth_status(binary: str = "claude") -> dict[str, Any] | None:
    """
    Real login-state confirmation, not just PATH presence -- `claude auth status`
    (verified live 2026-07-19) prints a JSON object: {"loggedIn": bool, "authMethod":
    str, "apiProvider": str, "email": str, "subscriptionType": str, ...}. Returns None
    on any failure (CLI missing, non-zero exit, unparseable output) rather than
    raising -- this is a best-effort confirm step for the connect flow, not something
    that should crash it.
    """
    try:
        result = subprocess.run([binary, "auth", "status"], capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        status = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return status if isinstance(status, dict) else None


def tools_for_policy(policy_document: dict[str, Any]) -> list[str]:
    """
    Map a policy document's permissions onto the Claude Code CLI tool allowlist
    (`--tools`). Deny-by-default: a tool is included only if its owning permission
    category is exactly "allow" -- same discipline as OpenCode's tools_for_policy,
    re-derived here rather than shared, because the concrete tool surface (and thus
    the mapping) is genuinely per-adapter (see native-cli-provider-adapters.md,
    "Permission mapping is per-adapter, not shared").

    - permissions.read    -> Read, Glob, Grep
    - permissions.edit    -> Edit, Write, NotebookEdit
    - permissions.execute -> Bash
    - permissions.network -> WebFetch, WebSearch

    "ask" is treated as denied, not as a softer form of allow -- there is no mid-run
    human-escalation mechanism yet to actually honor an "ask" prompt (same deliberate
    simplification as the OpenCode adapter).

    Returns a list, never the CLI's own `--tools ""` (disable-all) string form --
    callers pass an empty list straight to `--tools` and get the same disable-all
    effect through comma-joining.

    NOT enforced by this function, on purpose (same documented gap as OpenCode's
    tools_for_policy): permissions.external_paths (needs path-scoping tool call
    arguments -- `--add-dir` grants a directory, not a per-call constraint) and the
    policy document's limits/approvals fields (need run-duration and approval-gating
    mechanisms, not a tool allowlist).
    """
    permissions = policy_document.get("permissions", {})

    def granted(category: str) -> bool:
        return permissions.get(category) == "allow"

    tools: list[str] = []
    if granted("read"):
        tools += ["Read", "Glob", "Grep"]
    if granted("edit"):
        tools += ["Edit", "Write", "NotebookEdit"]
    if granted("execute"):
        tools.append("Bash")
    if granted("network"):
        tools += ["WebFetch", "WebSearch"]
    return tools


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def result_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    The terminal `result`-type message from a claude -p stream-json run -- shared
    between ClaudeCodeAdapter (task execution) and esc_exec.conversation (multi-turn
    conversations), both of which parse the same NDJSON stream shape.
    """
    for message in reversed(messages):
        if message.get("type") == "result":
            return message
    return None


class ClaudeCodeError(RuntimeError):
    pass


class ClaudeCodeClient:
    """
    Shells out to the real `claude` CLI in headless print mode. `--tools` is the
    actual enforcement boundary (a tool absent from the list literally isn't
    available to the model), so `--permission-mode bypassPermissions` is safe here --
    it only skips the interactive confirmation prompt a human can't answer in headless
    execution, it does not widen the tool allowlist. Verified live 2026-07-19 against
    claude-code 2.1.215: `--output-format stream-json` in print mode requires
    `--verbose` (undocumented in `--help`, confirmed by the CLI's own runtime error).
    """

    def __init__(self, binary: str = "claude", timeout: float = 600.0):
        self.binary, self.timeout = binary, timeout

    def _invoke(
        self, directory: Path, prompt: str, tools: list[str], output_format: str,
        model: str | None = None, resume_session_id: str | None = None, verbose: bool = False,
    ) -> str:
        command = [
            self.binary, "-p", "--output-format", output_format,
            "--permission-mode", "bypassPermissions",
            "--tools", ",".join(tools),
        ]
        if verbose:
            command.append("--verbose")
        if model:
            command += ["--model", model]
        if resume_session_id:
            command += ["--resume", resume_session_id]
        try:
            result = subprocess.run(
                command, cwd=directory, input=prompt, capture_output=True, text=True, timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            raise ClaudeCodeError(f"`{self.binary}` not found -- is Claude Code installed and on PATH?") from exc
        except subprocess.TimeoutExpired as exc:
            raise ClaudeCodeError(f"claude -p timed out after {self.timeout}s") from exc
        if result.returncode != 0:
            raise ClaudeCodeError(f"claude -p exited {result.returncode}: {result.stderr.strip()[:500]}")
        return result.stdout

    def run(
        self, directory: Path, prompt: str, tools: list[str],
        model: str | None = None, resume_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        The full-fidelity path: NDJSON message stream (init/assistant/user/result),
        used by ClaudeCodeAdapter.execute for real task runs where per-tool events
        matter. `--verbose` is required alongside stream-json in print mode (verified
        live 2026-07-19 against claude-code 2.1.215, undocumented in --help).
        """
        stdout = self._invoke(directory, prompt, tools, "stream-json", model, resume_session_id, verbose=True)
        messages: list[dict[str, Any]] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ClaudeCodeError(f"could not parse claude -p stream-json line: {line[:200]}") from exc
        return messages

    def ask(self, directory: Path, prompt: str, tools: list[str], model: str | None = None) -> dict[str, Any]:
        """
        The lightweight path: a single aggregated JSON result object, no per-tool
        event stream, no --verbose requirement. For a bounded question-answering call
        (e.g. suggest_purposes below) that isn't a task run at all -- no run.json, no
        events.jsonl, no verification plan, none of ClaudeCodeAdapter.execute's
        artifact machinery. Same underlying `result`-message shape `run()` gets as its
        last stream-json line, just without needing to stream to get it.
        """
        stdout = self._invoke(directory, prompt, tools, "json", model)
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ClaudeCodeError(f"could not parse claude -p json output: {stdout[:200]}") from exc


class ClaudeCodeAdapter:
    def __init__(self, client: ClaudeCodeClient, registry_path: Path):
        self.client, self.registry_path = client, registry_path

    def execute(self, task_path: Path, workspace_path: Path, adapter_path: Path, policy_path: Path, session_id: str | None = None) -> Path:
        for kind, path in (("task", task_path), ("workspace", workspace_path), ("adapter", adapter_path), ("policy", policy_path)):
            result = validate_contract(kind, path)
            if result.state != ManifestState.VALID:
                raise ValueError(f"Invalid {kind}: {'; '.join(result.messages)}")
        task_doc, workspace = load_yaml(task_path), load_yaml(workspace_path)["workspace"]
        policy_document = load_yaml(policy_path)
        task, adapter, policy = task_doc["task"], load_yaml(adapter_path)["adapter"], policy_document["policy"]
        if adapter["provider"] != "claude-code" or adapter["kind"] != "agent-runtime":
            raise ValueError("Adapter must be claude-code agent-runtime")
        if workspace["repository"] != task["repository"]:
            raise ValueError("Workspace repository must match task repository")
        started = time.monotonic()
        repository = resolve_route(self.registry_path, "repositories", task["repository"])
        run_id, created_at = f"run-{uuid.uuid4().hex}", _now()
        run_dir = repository / ".esc-ai" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        context = build_task_context(repository, task_path, run_dir / "task-context.json", registry_path=self.registry_path)
        events: list[dict[str, Any]] = []
        messages: list[dict[str, Any]] = []
        self._event(events, run_id, "run.started", "orchestrator", {"task_id": task["id"]})
        artifact_name: str | None = None
        tool_grant = tools_for_policy(policy_document)
        instruction_bundle, extension_conflicts = build_instruction_bundle(repository, context, policy)
        if extension_conflicts:
            raise ValueError(
                "project-specific extension declares document ID(s) under a reserved "
                f"architecture-framework prefix: {', '.join(extension_conflicts)}"
            )
        write_json(run_dir / "instruction-bundle.json", {"schema_version": 1, "levels": instruction_bundle})
        claude_session_id = session_id
        try:
            messages = self.client.run(
                repository, self._prompt(context, tool_grant), tool_grant,
                adapter.get("configuration", {}).get("model"), resume_session_id=session_id,
            )
            outcome = result_message(messages)
            if outcome is None:
                raise ClaudeCodeError("claude -p stream produced no terminal `result` message")
            claude_session_id = outcome.get("session_id", claude_session_id)
            if outcome.get("is_error"):
                raise ClaudeCodeError(str(outcome.get("result"))[:500])
            summary = outcome.get("result") or ""
            if not summary:
                raise ClaudeCodeError("claude -p returned no result text")
            for tool_event in self._tool_events(messages):
                self._event(events, run_id, "tool.completed", "adapter", tool_event)
            self._event(events, run_id, "message.created", "agent", {"summary": summary})
            artifact_id, artifact_name = f"artifact-{uuid.uuid4().hex}", "artifact.json"
            write_json(run_dir / "summary.json", {"summary": summary, "provider": "claude-code"})
            write_json(run_dir / artifact_name, {"schema_version": 1, "artifact": {"id": artifact_id, "run_id": run_id, "kind": "report", "path": "summary.json", "media_type": "application/json", "retention": "transient", "created_at": _now()}})
            self._event(events, run_id, "artifact.created", "orchestrator", {"artifact_id": artifact_id})
            self._event(events, run_id, "run.completed", "orchestrator", {"status": "succeeded"})
            status = "succeeded"
        except Exception as exc:
            self._event(events, run_id, "run.failed", "orchestrator", {"error": str(exc)[:500]})
            status = "failed"
        self._write_events(run_dir / "events.jsonl", events)
        outcome = result_message(messages) or {}
        write_json(run_dir / "run.json", {"schema_version": 1, "run": {"id": run_id, "task_id": task["id"], "status": status, "created_at": created_at, "started_at": created_at, "ended_at": _now()}, "bindings": {"adapter": adapter["id"], "workspace": workspace["id"], "policy": policy["id"], "tool_grant": tool_grant, "instruction_bundle": "instruction-bundle.json"}, "events": "events.jsonl", "artifacts": [artifact_name] if artifact_name else [], "adapter_metadata": {"provider": "claude-code", "session_id": claude_session_id, "total_cost_usd": outcome.get("total_cost_usd"), "num_turns": outcome.get("num_turns")}})
        write_json(run_dir / "run-metrics.json", run_metrics(
            run_id, task["id"], "claude-code", status, run_dir / "task-context.json", context,
            round((time.monotonic() - started) * 1000), self._tool_events(messages), self._token_response(outcome),
        ))
        if status == "failed":
            raise ClaudeCodeError(f"claude-code run failed; see {run_dir / 'events.jsonl'}")
        return run_dir

    @staticmethod
    def _token_response(outcome: dict[str, Any]) -> dict[str, Any]:
        """
        Translate claude -p's `result` message's `usage` block (input_tokens,
        cache_creation_input_tokens, cache_read_input_tokens, output_tokens) into the
        `{"info": {"tokens": {...}}}` envelope `measurement.token_metrics` expects --
        which is OpenCode's response shape, not a provider-neutral one. Per-adapter
        translation (same precedent as tools_for_policy) rather than changing shared
        code to know about every provider's own usage schema. Claude Code has no
        separate reasoning-token count (thinking tokens are folded into output_tokens),
        so reasoning is always 0 here, not missing.
        """
        usage = outcome.get("usage") or {}
        return {"info": {"tokens": {
            "input": usage.get("input_tokens"),
            "output": usage.get("output_tokens"),
            "reasoning": 0,
            "cache": {
                "read": usage.get("cache_read_input_tokens"),
                "write": usage.get("cache_creation_input_tokens"),
            },
        }}}

    @staticmethod
    def _tool_events(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Pair each `tool_use` block (assistant messages) with its matching
        `tool_result` (subsequent user message, linked by `tool_use_id`) to produce
        OpenCode-shaped `{tool, status, title}` entries -- same event shape either
        adapter produces, even though the two CLIs stream fundamentally different
        message formats.
        """
        results_by_id: dict[str, dict[str, Any]] = {}
        for message in messages:
            if message.get("type") != "user":
                continue
            for block in message.get("message", {}).get("content", []):
                if block.get("type") == "tool_result" and block.get("tool_use_id"):
                    results_by_id[block["tool_use_id"]] = block

        tool_events: list[dict[str, Any]] = []
        seen: set[str] = set()
        for message in messages:
            if message.get("type") != "assistant":
                continue
            for block in message.get("message", {}).get("content", []):
                if block.get("type") != "tool_use" or block.get("id") in seen:
                    continue
                seen.add(block["id"])
                result_block = results_by_id.get(block["id"], {})
                is_error = bool(result_block.get("is_error"))
                tool_input = block.get("input", {})
                title = next((str(v) for v in tool_input.values() if isinstance(v, str)), None)
                tool_events.append({
                    "tool": block.get("name"),
                    "status": "error" if is_error else "completed",
                    "title": title,
                })
        return tool_events

    @staticmethod
    def _tool_constraints(tool_grant: list[str]) -> str:
        edit = "Edit" in tool_grant
        execute = "Bash" in tool_grant
        network = "WebFetch" in tool_grant
        if not edit and not execute and not network:
            return "Operate read-only. Do not edit files, run shell commands, or access the network."
        return " ".join((
            "You may edit files." if edit else "Do not edit files.",
            "You may run shell commands." if execute else "Do not run shell commands.",
            "You may access the network." if network else "Do not access the network.",
        ))

    def _prompt(self, context: dict[str, Any], tool_grant: list[str]) -> str:
        components = context["routing"]["components"]
        lines = [
            f"Objective: {context['task']['objective']}",
            self._tool_constraints(tool_grant),
            f"Available tools: {', '.join(tool_grant) if tool_grant else 'none'}.",
            f"Declared components: {', '.join(component['id'] for component in components)}.",
            f"Read the repository index first: {context['routing']['repository_index']}.",
        ]
        for component in components:
            lines.append(f"Then read {component['index']} for component {component['id']}; search only: {', '.join(component['search_roots'])}.")
        if context["scope"]["paths"]:
            lines.append(f"Task paths: {', '.join(context['scope']['paths'])}.")
        return "\n".join(lines + ["Return a concise evidence-based result."])

    @staticmethod
    def _event(events: list[dict[str, Any]], run_id: str, event_type: str, actor: str, payload: dict[str, Any]) -> None:
        events.append({"schema_version": 1, "event": {"id": f"event-{uuid.uuid4().hex}", "run_id": run_id, "sequence": len(events), "timestamp": _now(), "type": event_type, "actor": actor, "payload": payload}})

    @staticmethod
    def _write_events(path: Path, events: list[dict[str, Any]]) -> None:
        path.write_text("".join(json.dumps(event, separators=(",", ":")) + "\n" for event in events), encoding="utf-8")


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
