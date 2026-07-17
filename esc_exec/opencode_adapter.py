from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from esc_exec.contracts import validate_contract
from esc_exec.json_io import write_json
from esc_exec.model import ManifestState
from esc_exec.registry import resolve_route
from esc_exec.task_context import build_task_context
from esc_exec.yaml_io import load_yaml

READ_ONLY_TOOLS = {"read": True, "list": True, "glob": True, "grep": True, "bash": False, "edit": False, "write": False, "patch": False, "webfetch": False, "task": False, "todowrite": False, "todoread": False}


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
    def prompt(self, directory: Path, session_id: str, prompt: str, model: dict[str, str] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"agent": "plan", "tools": READ_ONLY_TOOLS, "parts": [{"type": "text", "text": prompt}]}
        if model: body["model"] = {"providerID": model["provider_id"], "modelID": model["model_id"]}
        return self._request("POST", f"/session/{session_id}/message", directory, body)


class OpenCodeAdapter:
    def __init__(self, client: OpenCodeClient, registry_path: Path):
        self.client, self.registry_path = client, registry_path

    def execute(self, task_path: Path, workspace_path: Path, adapter_path: Path, policy_path: Path, output_root: Path, session_id: str | None = None) -> Path:
        for kind, path in (("task", task_path), ("workspace", workspace_path), ("adapter", adapter_path), ("policy", policy_path)):
            result = validate_contract(kind, path)
            if result.state != ManifestState.VALID: raise ValueError(f"Invalid {kind}: {'; '.join(result.messages)}")
        task_doc, workspace = load_yaml(task_path), load_yaml(workspace_path)["workspace"]
        task, adapter, policy = task_doc["task"], load_yaml(adapter_path)["adapter"], load_yaml(policy_path)["policy"]
        if adapter["provider"] != "opencode" or adapter["kind"] != "agent-runtime": raise ValueError("Adapter must be OpenCode agent-runtime")
        if workspace["repository"] != task["repository"]: raise ValueError("Workspace repository must match task repository")
        repository = resolve_route(self.registry_path, "repositories", task["repository"])
        self.client.health(repository)
        run_id, created_at = f"run-{uuid.uuid4().hex}", _now()
        run_dir = output_root / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        context = build_task_context(repository, task_path, run_dir / "task-context.json")
        if session_id is None: session_id = self.client.create_session(repository, task["title"])["id"]
        events: list[dict[str, Any]] = []
        self._event(events, run_id, "run.started", "orchestrator", {"task_id": task["id"]})
        artifact_name: str | None = None
        try:
            response = self.client.prompt(repository, session_id, self._prompt(context), adapter.get("configuration", {}).get("model"))
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
        write_json(run_dir / "run.json", {"schema_version": 1, "run": {"id": run_id, "task_id": task["id"], "status": status, "created_at": created_at, "started_at": created_at, "ended_at": _now()}, "bindings": {"adapter": adapter["id"], "workspace": workspace["id"], "policy": policy["id"]}, "events": "events.jsonl", "artifacts": [artifact_name] if artifact_name else [], "adapter_metadata": {"provider": "opencode", "session_id": session_id, "server": self.client.base_url}})
        if status == "failed": raise OpenCodeError(f"OpenCode run failed; see {run_dir / 'events.jsonl'}")
        return run_dir

    def fork(self, repository_id: str, session_id: str) -> str:
        return self.client.fork(resolve_route(self.registry_path, "repositories", repository_id), session_id)["id"]

    @staticmethod
    def _prompt(context: dict[str, Any]) -> str:
        components = context["routing"]["components"]
        lines = [
            f"Objective: {context['task']['objective']}",
            "Operate read-only. Do not edit files, run shell commands, or access the network.",
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
