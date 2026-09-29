from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from esc_exec.architecture_lookup import architecture_prompt_lines
from esc_exec.claude_client import (  # noqa: F401 -- compatibility re-export
    ClaudeCodeClient,
    ClaudeCodeError,
    _now,
    claude_auth_status,
    claude_cli_available,
    result_message,
)
from esc_exec.claude_policy import (  # noqa: F401 -- compatibility re-export
    HARD_DENY_SETTINGS,
    granted_categories,
    tools_for_policy,
)
from esc_exec.contracts import validate_contract
from esc_exec.instructions import build_instruction_bundle
from esc_exec.json_io import write_json
from esc_exec.manifests import repository_manifest_path
from esc_exec.measurement import run_metrics
from esc_exec.model import ManifestState
from esc_exec.registry import resolve_route
from esc_exec.roadmap import roadmap_prompt_line
from esc_exec.task_context import build_task_context
from esc_exec.worktree import (
    copy_inherited_files,
    ensure_worktree,
    finalize_worktree,
    worktree_branch,
)
from esc_exec.yaml_io import load_yaml


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
        # workspace.kind == "worktree" -- see
        # plan/future/pre-flight-consent-and-bounded-autonomy.md layer 4: the agent
        # edits a disposable worktree, not the live checkout, so an unanticipated
        # change is contained and reviewable rather than needing to be prevented
        # mid-run. Context is still built from `repository`, not the worktree --
        # architecture indexes/manifests are identical at worktree-creation time,
        # and staying off the mutable worktree here keeps this resolution stable
        # across retries of the same task.
        if workspace["kind"] == "worktree":
            execution_root = ensure_worktree(repository, task["id"])
            # Opt-in gitignored-file inheritance -- see
            # plan/active/pre-flight-doctor-and-gate-prerequisites.md. A fresh
            # worktree's git checkout never contains gitignored local config
            # (local.properties, .env); a repository can declare which of those
            # to copy in so a build that depends on one doesn't fail the same
            # way on every task run.
            repository_manifest = load_yaml(repository_manifest_path(repository))
            copy_inherited_files(repository, execution_root, repository_manifest.get("worktree_inherit") or [])
        else:
            execution_root = repository
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
                execution_root, self._prompt(context, tool_grant, repository), tool_grant,
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
        except Exception as exc:  # noqa: BLE001 -- provider boundary: any failure becomes a recorded failed run
            self._event(events, run_id, "run.failed", "orchestrator", {"error": str(exc)[:500]})
            status = "failed"
        self._write_events(run_dir / "events.jsonl", events)
        outcome = result_message(messages) or {}
        # See plan/future/pre-flight-consent-and-bounded-autonomy.md layer 6: a
        # `HARD_DENY_SETTINGS` hit (or any other Claude Code permission check)
        # doesn't necessarily set is_error -- the model often just narrates
        # around it and finishes normally (verified live 2026-07-24: a denied
        # `rm -rf` produced a clean `terminal_reason: "completed"` result with
        # the denial only visible in `permission_denials`). Recorded here as its
        # own artifact, deliberately not folded into `status` above -- this
        # adapter's own honest report is "the agent didn't crash," same
        # precedent as a not-clean verification result leaving run.json's own
        # status as "succeeded" and letting the orchestrator's Scheduler (which
        # sees this artifact, not this function) decide the Store-level status
        # (see scheduler.py's _permission_denials/_work).
        write_json(run_dir / "permission-denials.json", {
            "schema_version": 1, "task_id": task["id"], "generated_at": _now(),
            "denials": [
                {"tool_name": denial.get("tool_name"), "tool_input": denial.get("tool_input")}
                for denial in (outcome.get("permission_denials") or [])
            ],
        })
        worktree_info = None
        if workspace["kind"] == "worktree":
            # Runs regardless of success/failure -- even a failed run may have left
            # real edits worth reviewing, and one that changed nothing gets cleaned
            # up automatically either way.
            kept = finalize_worktree(repository, task["id"], f"escape-ai task {task['id']} ({status})")
            worktree_info = {"branch": worktree_branch(task["id"]), "kept": kept}
        # See plan/future/pre-flight-consent-and-bounded-autonomy.md layer 1:
        # recorded unconditionally, same treatment as tool_grant -- an honest
        # record of what categories this run actually operated under, not a
        # decision about whether a human needed to be asked (that's the
        # orchestrator CLI's call, made before dispatch, from this same field
        # on a task's prior runs).
        consent = {"granted_categories": granted_categories(policy_document), "granted_at": created_at}
        write_json(run_dir / "run.json", {"schema_version": 1, "run": {"id": run_id, "task_id": task["id"], "status": status, "created_at": created_at, "started_at": created_at, "ended_at": _now()}, "bindings": {"adapter": adapter["id"], "workspace": workspace["id"], "policy": policy["id"], "tool_grant": tool_grant, "instruction_bundle": "instruction-bundle.json", "worktree": worktree_info, "consent": consent}, "events": "events.jsonl", "artifacts": [artifact_name] if artifact_name else [], "adapter_metadata": {"provider": "claude-code", "session_id": claude_session_id, "total_cost_usd": outcome.get("total_cost_usd"), "num_turns": outcome.get("num_turns")}})
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

    def _prompt(self, context: dict[str, Any], tool_grant: list[str], repository: Path) -> str:
        components = context["routing"]["components"]
        lines = [f"Objective: {context['task']['objective']}"]
        roadmap_line = roadmap_prompt_line(repository)
        if roadmap_line:
            lines.append(roadmap_line)
        lines += [
            self._tool_constraints(tool_grant),
            f"Available tools: {', '.join(tool_grant) if tool_grant else 'none'}.",
            f"Declared components: {', '.join(component['id'] for component in components)}.",
            f"Read the repository index first: {context['routing']['repository_index']}.",
        ]
        for component in components:
            lines.append(f"Then read {component['index']} for component {component['id']}; search only: {', '.join(component['search_roots'])}.")
            lines += architecture_prompt_lines(component)
        if context["scope"]["paths"]:
            lines.append(f"Task paths: {', '.join(context['scope']['paths'])}.")
        return "\n".join(lines + ["Return a concise evidence-based result."])

    @staticmethod
    def _event(events: list[dict[str, Any]], run_id: str, event_type: str, actor: str, payload: dict[str, Any]) -> None:
        events.append({"schema_version": 1, "event": {"id": f"event-{uuid.uuid4().hex}", "run_id": run_id, "sequence": len(events), "timestamp": _now(), "type": event_type, "actor": actor, "payload": payload}})

    @staticmethod
    def _write_events(path: Path, events: list[dict[str, Any]]) -> None:
        path.write_text("".join(json.dumps(event, separators=(",", ":")) + "\n" for event in events), encoding="utf-8")

