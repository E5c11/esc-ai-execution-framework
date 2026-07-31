from __future__ import annotations

import json
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
from esc_exec.registry import resolve_route
from esc_exec.task_context import build_task_context
from esc_exec.yaml_io import load_yaml
from esc_exec.measurement import run_metrics


def codex_cli_available(binary: str = "codex") -> bool:
    """Just a PATH check -- see codex_auth_status for a real login-state check."""
    return shutil.which(binary) is not None


def codex_auth_status(binary: str = "codex") -> str | None:
    """
    Real login-state confirmation, not just PATH presence. `codex login status`
    (verified live 2026-07-19) has no structured/JSON output -- just plain text, e.g.
    "Logged in using ChatGPT" on success -- and, confirmed live, prints it to
    **stderr**, not stdout (a real bug caught by live-testing the actual connect
    flow, not just unit tests against an assumed shape: reading only stdout returned
    None even while logged in). Checks stdout first, falls back to stderr. Returns
    None on any failure (CLI missing, non-zero exit, timeout, both streams empty) --
    best-effort confirm step, never raises.
    """
    try:
        result = subprocess.run([binary, "login", "status"], capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or result.stderr.strip() or None


def sandbox_for_policy(policy_document: dict[str, Any]) -> str:
    """
    Map a policy document's permissions onto Codex's `-s/--sandbox` value. Coarser
    than tools_for_policy on purpose, not by oversight: Codex executes everything as
    actual shell commands (see CodexClient's docstring), so "edit without execute" or
    "execute without edit" cannot be separately granted the way Claude Code's
    Edit/Bash tool split allows -- the moment either permission is `allow`, both
    become possible, because file edits happen via shell too.

    - read-only:       neither edit nor execute is allow (the default, safest case)
    - workspace-write: edit or execute is allow
    - danger-full-access is never auto-selected -- no policy category maps to it,
      matching this project's deny-by-default discipline.

    NOT enforced by this function, on purpose (same documented gap as the other
    adapters): permissions.network has no independently-verified Codex sandbox
    equivalent, and permissions.external_paths/limits/approvals aren't touched here
    either.
    """
    permissions = policy_document.get("permissions", {})
    if permissions.get("edit") == "allow" or permissions.get("execute") == "allow":
        return "workspace-write"
    return "read-only"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CodexError(RuntimeError):
    pass


class CodexClient:
    """
    Shells out to the real `codex` CLI in headless exec mode. Verified live
    2026-07-19 against codex-cli 0.144.1:

    - `-a/--ask-for-approval` is a top-level flag, not an `exec` subcommand flag --
      `codex exec -a never ...` fails ("unexpected argument '-a' found"); the working
      form is `codex -a never exec ...`. Always `never` here, same reasoning as Claude
      Code's `bypassPermissions`: there's no human present to approve anything in
      headless execution, and the sandbox is the real enforcement boundary regardless
      of whether prompts are asked.
    - `--skip-git-repo-check` is required outside a git repository. Real target
      repositories are always git repos, so this mostly matters for scratch testing,
      but it's passed unconditionally since it's a no-op inside a real repo.
    - stdin must be explicitly closed (`stdin=subprocess.DEVNULL`), not left open --
      Codex's docs say piped stdin gets appended as a `<stdin>` block alongside a
      positional prompt; leaving stdin open/inherited makes it hang indefinitely
      waiting for EOF instead of treating "nothing piped" as "no additional input."
      Confirmed by hitting the hang live before adding this, not assumed.
    - Executes everything as real shell commands under the sandbox policy (observed
      directly: a live run's `command_execution` item ran `/bin/bash -lc '...'`), not
      discrete typed tool calls -- this is why sandbox_for_policy is coarser than
      tools_for_policy.
    """

    def __init__(self, binary: str = "codex", timeout: float = 1800.0):
        self.binary, self.timeout = binary, timeout

    def run(self, directory: Path, prompt: str, sandbox: str, model: str | None = None) -> list[dict[str, Any]]:
        command = [self.binary, "-a", "never", "exec", "-s", sandbox, "--skip-git-repo-check", "--json"]
        if model:
            command += ["-m", model]
        command.append(prompt)
        try:
            result = subprocess.run(
                command, cwd=directory, capture_output=True, text=True, timeout=self.timeout,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise CodexError(f"`{self.binary}` not found -- is Codex CLI installed and on PATH?") from exc
        except subprocess.TimeoutExpired as exc:
            raise CodexError(f"codex exec timed out after {self.timeout}s") from exc
        if result.returncode != 0:
            raise CodexError(f"codex exec exited {result.returncode}: {result.stderr.strip()[:500]}")
        messages: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise CodexError(f"could not parse codex exec json line: {line[:200]}") from exc
        return messages


class CodexAdapter:
    def __init__(self, client: CodexClient, registry_path: Path):
        self.client, self.registry_path = client, registry_path

    def execute(self, task_path: Path, workspace_path: Path, adapter_path: Path, policy_path: Path, session_id: str | None = None) -> Path:
        for kind, path in (("task", task_path), ("workspace", workspace_path), ("adapter", adapter_path), ("policy", policy_path)):
            result = validate_contract(kind, path)
            if result.state != ManifestState.VALID:
                raise ValueError(f"Invalid {kind}: {'; '.join(result.messages)}")
        task_doc, workspace = load_yaml(task_path), load_yaml(workspace_path)["workspace"]
        policy_document = load_yaml(policy_path)
        task, adapter, policy = task_doc["task"], load_yaml(adapter_path)["adapter"], policy_document["policy"]
        if adapter["provider"] != "codex" or adapter["kind"] != "agent-runtime":
            raise ValueError("Adapter must be codex agent-runtime")
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
        sandbox = sandbox_for_policy(policy_document)
        instruction_bundle, extension_conflicts = build_instruction_bundle(repository, context, policy)
        if extension_conflicts:
            raise ValueError(
                "project-specific extension declares document ID(s) under a reserved "
                f"architecture-framework prefix: {', '.join(extension_conflicts)}"
            )
        write_json(run_dir / "instruction-bundle.json", {"schema_version": 1, "levels": instruction_bundle})
        try:
            messages = self.client.run(
                repository, self._prompt(context, sandbox), sandbox, adapter.get("configuration", {}).get("model"),
            )
            outcome = self._outcome(messages)
            if outcome["is_error"]:
                raise CodexError(outcome["error_detail"] or "codex exec produced no agent_message result")
            summary = outcome["text"] or ""
            if not summary:
                raise CodexError("codex exec returned no result text")
            for tool_event in self._tool_events(messages):
                self._event(events, run_id, "tool.completed", "adapter", tool_event)
            self._event(events, run_id, "message.created", "agent", {"summary": summary})
            artifact_id, artifact_name = f"artifact-{uuid.uuid4().hex}", "artifact.json"
            write_json(run_dir / "summary.json", {"summary": summary, "provider": "codex"})
            write_json(run_dir / artifact_name, {"schema_version": 1, "artifact": {"id": artifact_id, "run_id": run_id, "kind": "report", "path": "summary.json", "media_type": "application/json", "retention": "transient", "created_at": _now()}})
            self._event(events, run_id, "artifact.created", "orchestrator", {"artifact_id": artifact_id})
            self._event(events, run_id, "run.completed", "orchestrator", {"status": "succeeded"})
            status = "succeeded"
        except Exception as exc:
            self._event(events, run_id, "run.failed", "orchestrator", {"error": str(exc)[:500]})
            status = "failed"
        self._write_events(run_dir / "events.jsonl", events)
        outcome = self._outcome(messages)
        write_json(run_dir / "run.json", {"schema_version": 1, "run": {"id": run_id, "task_id": task["id"], "status": status, "created_at": created_at, "started_at": created_at, "ended_at": _now()}, "bindings": {"adapter": adapter["id"], "workspace": workspace["id"], "policy": policy["id"], "sandbox": sandbox, "instruction_bundle": "instruction-bundle.json"}, "events": "events.jsonl", "artifacts": [artifact_name] if artifact_name else [], "adapter_metadata": {"provider": "codex", "thread_id": outcome["thread_id"]}})
        write_json(run_dir / "run-metrics.json", run_metrics(
            run_id, task["id"], "codex", status, run_dir / "task-context.json", context,
            round((time.monotonic() - started) * 1000), self._tool_events(messages), self._token_response(outcome),
        ))
        if status == "failed":
            raise CodexError(f"codex run failed; see {run_dir / 'events.jsonl'}")
        return run_dir

    @staticmethod
    def _token_response(outcome: dict[str, Any]) -> dict[str, Any]:
        """
        Translate codex exec's `turn.completed` usage block (input_tokens,
        cached_input_tokens, output_tokens, reasoning_output_tokens) into the
        `{"info": {"tokens": {...}}}` envelope measurement.token_metrics expects.
        Codex has no cache-write concept (only cached_input_tokens, i.e. cache reads),
        so cache.write is always 0, not missing.
        """
        usage = outcome.get("usage") or {}
        return {"info": {"tokens": {
            "input": usage.get("input_tokens"),
            "output": usage.get("output_tokens"),
            "reasoning": usage.get("reasoning_output_tokens", 0),
            "cache": {
                "read": usage.get("cached_input_tokens"),
                "write": 0,
            },
        }}}

    @staticmethod
    def _outcome(messages: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Normalize codex exec's event stream into a single result shape, mirroring
        ClaudeCodeAdapter's `_result_message` but for a structurally different
        schema: no single terminal "result" message -- the final answer is the last
        `item.completed` event whose item.type is "agent_message", usage comes from a
        separate `turn.completed` event, and an explicit `type: "error"` event (if
        present) means failure regardless of whether an agent_message also appeared.
        """
        thread_id = next((message.get("thread_id") for message in messages if message.get("type") == "thread.started"), None)
        usage = next((message.get("usage") for message in messages if message.get("type") == "turn.completed"), None)
        error_message = next((message for message in messages if message.get("type") == "error"), None)
        text = None
        for message in messages:
            if message.get("type") == "item.completed" and message.get("item", {}).get("type") == "agent_message":
                text = message["item"].get("text")
        is_error = error_message is not None or (text is None and usage is None)
        return {
            "thread_id": thread_id,
            "text": text,
            "usage": usage or {},
            "is_error": is_error,
            "error_detail": str(error_message.get("message"))[:500] if error_message else None,
        }

    @staticmethod
    def _tool_events(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tool_events: list[dict[str, Any]] = []
        for message in messages:
            if message.get("type") != "item.completed":
                continue
            item = message.get("item", {})
            if item.get("type") != "command_execution":
                continue
            tool_events.append({
                "tool": "command_execution",
                "status": "completed" if item.get("exit_code") == 0 else "error",
                "title": item.get("command"),
            })
        return tool_events

    @staticmethod
    def _sandbox_constraints(sandbox: str) -> str:
        if sandbox == "read-only":
            return "Operate read-only. Do not edit files or run commands that modify the workspace."
        if sandbox == "workspace-write":
            return "You may edit files and run shell commands within the workspace."
        return "You have full workspace and system access -- use it only as strictly necessary."

    def _prompt(self, context: dict[str, Any], sandbox: str) -> str:
        components = context["routing"]["components"]
        lines = [
            f"Objective: {context['task']['objective']}",
            self._sandbox_constraints(sandbox),
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
