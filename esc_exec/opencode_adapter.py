from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from esc_exec.contracts import validate_contract
from esc_exec.instructions import check_extension_namespace_conflict, order_instruction_bundle
from esc_exec.json_io import write_json
from esc_exec.manifests import REPOSITORY_MANIFEST
from esc_exec.model import ManifestState
from esc_exec.registry import resolve_route
from esc_exec.task_context import build_task_context
from esc_exec.workflow_bootstrap import WORKFLOW_README_PATH, parse_workflow_policy_frontmatter
from esc_exec.yaml_io import load_yaml
from esc_exec.measurement import run_metrics

def tools_for_policy(policy_document: dict[str, Any]) -> dict[str, bool]:
    """
    Map a policy document's permissions onto the OpenCode tool grant actually sent
    with a run's prompt. Deny-by-default: a tool is granted only if its owning
    permission category is exactly "allow".

    - permissions.read   -> read, list, glob, grep
    - permissions.edit   -> edit, write, patch
    - permissions.execute -> bash
    - permissions.network -> webfetch

    "ask" is treated as denied, not as a softer form of allow. There is no mid-run
    human-escalation mechanism yet to actually honor an "ask" prompt, and silently
    auto-approving it would be worse than denying it outright -- that's a known,
    deliberate simplification, not an oversight.

    task/todowrite/todoread aren't covered by any policy.schema.yaml permission
    category, so they stay denied unconditionally rather than inventing a mapping
    for them.

    NOT enforced by this function, on purpose: permissions.external_paths (needs
    path-scoping the tool call arguments, not a tool on/off flag) and the policy
    document's limits/approvals fields (need run-duration and approval-gating
    mechanisms, not a tool grant). A policy that declares these gets them schema-
    validated, not behaviorally enforced -- that gap is real and this function does
    not close it.
    """
    permissions = policy_document.get("permissions", {})
    def granted(category: str) -> bool:
        return permissions.get(category) == "allow"
    read, edit, execute, network = granted("read"), granted("edit"), granted("execute"), granted("network")
    return {
        "read": read, "list": read, "glob": read, "grep": read,
        "bash": execute,
        "edit": edit, "write": edit, "patch": edit,
        "webfetch": network,
        "task": False, "todowrite": False, "todoread": False,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class OpenCodeError(RuntimeError):
    pass


class OpenCodeClient:
    def __init__(self, base_url: str, timeout: float = 120.0):
        self.base_url, self.timeout = base_url.rstrip("/"), timeout

    def _request(self, method: str, path: str, directory: Path, body: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}?{urlencode({'directory': str(directory)})}"
        request = Request(url, data=json.dumps(body).encode() if body is not None else None, method=method, headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode())
        except HTTPError as exc:
            raise OpenCodeError(f"OpenCode HTTP {exc.code}: {exc.read().decode(errors='replace')[:500]}") from exc
        except (URLError, TimeoutError) as exc:
            raise OpenCodeError(f"Cannot reach OpenCode server at {self.base_url}: {exc}") from exc

    def health(self, directory: Path) -> dict[str, Any]: return self._request("GET", "/project/current", directory)
    def create_session(self, directory: Path, title: str) -> dict[str, Any]: return self._request("POST", "/session", directory, {"title": title})
    def fork(self, directory: Path, session_id: str) -> dict[str, Any]: return self._request("POST", f"/session/{session_id}/fork", directory, {})
    def prompt(self, directory: Path, session_id: str, prompt: str, tools: dict[str, bool], model: dict[str, str] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"agent": "plan", "tools": tools, "parts": [{"type": "text", "text": prompt}]}
        if model: body["model"] = {"providerID": model["provider_id"], "modelID": model["model_id"]}
        return self._request("POST", f"/session/{session_id}/message", directory, body)


class OpenCodeAdapter:
    def __init__(self, client: OpenCodeClient, registry_path: Path):
        self.client, self.registry_path = client, registry_path

    def execute(self, task_path: Path, workspace_path: Path, adapter_path: Path, policy_path: Path, session_id: str | None = None) -> Path:
        for kind, path in (("task", task_path), ("workspace", workspace_path), ("adapter", adapter_path), ("policy", policy_path)):
            result = validate_contract(kind, path)
            if result.state != ManifestState.VALID: raise ValueError(f"Invalid {kind}: {'; '.join(result.messages)}")
        task_doc, workspace = load_yaml(task_path), load_yaml(workspace_path)["workspace"]
        policy_document = load_yaml(policy_path)
        task, adapter, policy = task_doc["task"], load_yaml(adapter_path)["adapter"], policy_document["policy"]
        if adapter["provider"] != "opencode" or adapter["kind"] != "agent-runtime": raise ValueError("Adapter must be OpenCode agent-runtime")
        if workspace["repository"] != task["repository"]: raise ValueError("Workspace repository must match task repository")
        started = time.monotonic()
        repository = resolve_route(self.registry_path, "repositories", task["repository"])
        self.client.health(repository)
        run_id, created_at = f"run-{uuid.uuid4().hex}", _now()
        run_dir = repository / ".esc-ai" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        context = build_task_context(repository, task_path, run_dir / "task-context.json", registry_path=self.registry_path)
        if session_id is None: session_id = self.client.create_session(repository, task["title"])["id"]
        events: list[dict[str, Any]] = []
        response: dict[str, Any] = {}
        tools: list[dict[str, Any]] = []
        self._event(events, run_id, "run.started", "orchestrator", {"task_id": task["id"]})
        artifact_name: str | None = None
        tool_grant = tools_for_policy(policy_document)
        instruction_bundle, extension_conflicts = self._instruction_bundle(repository, context, policy)
        if extension_conflicts:
            raise ValueError(
                "project-specific extension declares document ID(s) under a reserved "
                f"architecture-framework prefix: {', '.join(extension_conflicts)}"
            )
        write_json(run_dir / "instruction-bundle.json", {"schema_version": 1, "levels": instruction_bundle})
        try:
            response = self.client.prompt(repository, session_id, self._prompt(context, tool_grant), tool_grant, adapter.get("configuration", {}).get("model"))
            if response.get("info", {}).get("error"):
                raise OpenCodeError(str(response["info"]["error"])[:500])
            summary, tools = self._summarize(response)
            if not summary:
                raise OpenCodeError("OpenCode returned no assistant text")
            for tool in tools: self._event(events, run_id, "tool.completed", "adapter", tool)
            self._event(events, run_id, "message.created", "agent", {"summary": summary})
            artifact_id, artifact_name = f"artifact-{uuid.uuid4().hex}", "artifact.json"
            write_json(run_dir / "summary.json", {"summary": summary, "provider": "opencode"})
            write_json(run_dir / artifact_name, {"schema_version": 1, "artifact": {"id": artifact_id, "run_id": run_id, "kind": "report", "path": "summary.json", "media_type": "application/json", "retention": "transient", "created_at": _now()}})
            self._event(events, run_id, "artifact.created", "orchestrator", {"artifact_id": artifact_id})
            self._event(events, run_id, "run.completed", "orchestrator", {"status": "succeeded"})
            status = "succeeded"
        except Exception as exc:
            self._event(events, run_id, "run.failed", "orchestrator", {"error": str(exc)[:500]})
            status = "failed"
        self._write_events(run_dir / "events.jsonl", events)
        write_json(run_dir / "run.json", {"schema_version": 1, "run": {"id": run_id, "task_id": task["id"], "status": status, "created_at": created_at, "started_at": created_at, "ended_at": _now()}, "bindings": {"adapter": adapter["id"], "workspace": workspace["id"], "policy": policy["id"], "tool_grant": tool_grant, "instruction_bundle": "instruction-bundle.json"}, "events": "events.jsonl", "artifacts": [artifact_name] if artifact_name else [], "adapter_metadata": {"provider": "opencode", "session_id": session_id, "server": self.client.base_url}})
        write_json(run_dir / "run-metrics.json", run_metrics(
            run_id, task["id"], "opencode", status, run_dir / "task-context.json", context,
            round((time.monotonic() - started) * 1000), tools, response,
        ))
        if status == "failed": raise OpenCodeError(f"OpenCode run failed; see {run_dir / 'events.jsonl'}")
        return run_dir

    def fork(self, repository_id: str, session_id: str) -> str:
        return self.client.fork(resolve_route(self.registry_path, "repositories", repository_id), session_id)["id"]

    @staticmethod
    def _instruction_bundle(
        repository: Path, context: dict[str, Any], policy: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """
        Compose the plan's precedence-ordered instruction bundle (see
        esc_exec.instructions) for this run, and check it for the one named conflict
        case: a project-specific workflow-policy extension declaring a document ID
        under a prefix the architecture framework owns.

        The extension-conflict check is always a no-op today: .esc-ai/workflows/
        README.md's policy.extension frontmatter (see workflow_bootstrap.py) only
        declares an id/precedence note, not an enumerated list of document IDs, so
        there is nothing to check yet. This wiring is real and will start doing
        something the moment that declaration mechanism grows a document-ID list --
        it is not fabricating a source that doesn't exist to look finished.
        """
        bundle: dict[str, list[str]] = {}

        policy_id = policy.get("id")
        bundle["safety_and_operator_policy"] = [f"policy:{policy_id}"] if policy_id else []

        repository_manifest_path = repository / REPOSITORY_MANIFEST
        frameworks = {}
        if repository_manifest_path.is_file():
            frameworks = load_yaml(repository_manifest_path).get("frameworks") or {}
        execution_framework_id = "esc-ai-execution-framework"
        bundle["execution_framework_core"] = [
            f"{execution_framework_id}:{frameworks[execution_framework_id]}"
            if execution_framework_id in frameworks else execution_framework_id
        ]

        architecture_docs: list[str] = []
        for component in context["routing"]["components"]:
            for document in component.get("architecture", {}).get("documents", []):
                if document["id"] not in architecture_docs:
                    architecture_docs.append(document["id"])
        bundle["architecture_framework_core_and_profile"] = architecture_docs

        extension_doc_ids: list[str] = []
        workflow_sources: list[str] = []
        workflow_readme_path = repository / WORKFLOW_README_PATH
        if workflow_readme_path.is_file():
            workflow_sources.append(str(WORKFLOW_README_PATH))
            frontmatter = parse_workflow_policy_frontmatter(workflow_readme_path.read_text(encoding="utf-8"))
            extension = frontmatter.get("policy", {}).get("extension")
            if isinstance(extension, dict) and extension.get("id"):
                workflow_sources.append(f"extension:{extension['id']}")
        bundle["repository_instructions_and_workflow_policy"] = workflow_sources

        bundle["component_manifests_and_profiles"] = [
            component["manifest"] for component in context["routing"]["components"]
        ]
        bundle["active_workflow_task_specification"] = [context["task"]["id"]]

        return order_instruction_bundle(bundle), check_extension_namespace_conflict(extension_doc_ids)

    @staticmethod
    def _tool_constraints(tool_grant: dict[str, bool]) -> str:
        if not tool_grant["edit"] and not tool_grant["bash"] and not tool_grant["webfetch"]:
            return "Operate read-only. Do not edit files, run shell commands, or access the network."
        return " ".join((
            "You may edit files." if tool_grant["edit"] else "Do not edit files.",
            "You may run shell commands." if tool_grant["bash"] else "Do not run shell commands.",
            "You may access the network." if tool_grant["webfetch"] else "Do not access the network.",
        ))

    @staticmethod
    def _prompt(context: dict[str, Any], tool_grant: dict[str, bool]) -> str:
        components = context["routing"]["components"]
        lines = [
            f"Objective: {context['task']['objective']}",
            OpenCodeAdapter._tool_constraints(tool_grant),
            f"Declared components: {', '.join(component['id'] for component in components)}.",
            f"Read the repository index first: {context['routing']['repository_index']}.",
        ]
        for component in components:
            lines.append(f"Then read {component['index']} for component {component['id']}; search only: {', '.join(component['search_roots'])}.")
        if context["scope"]["paths"]:
            lines.append(f"Task paths: {', '.join(context['scope']['paths'])}.")
        return "\n".join(lines + ["Return a concise evidence-based result."])

    @staticmethod
    def _summarize(response: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        texts, tools = [], []
        for part in response.get("parts", []):
            if part.get("type") == "text" and part.get("text"): texts.append(part["text"])
            elif part.get("type") == "tool":
                state = part.get("state", {})
                tools.append({"tool": part.get("tool"), "status": state.get("status"), "title": state.get("title")})
        return "\n".join(texts).strip(), tools

    @staticmethod
    def _event(events: list[dict[str, Any]], run_id: str, event_type: str, actor: str, payload: dict[str, Any]) -> None:
        events.append({"schema_version": 1, "event": {"id": f"event-{uuid.uuid4().hex}", "run_id": run_id, "sequence": len(events), "timestamp": _now(), "type": event_type, "actor": actor, "payload": payload}})

    @staticmethod
    def _write_events(path: Path, events: list[dict[str, Any]]) -> None:
        path.write_text("".join(json.dumps(event, separators=(",", ":")) + "\n" for event in events), encoding="utf-8")
