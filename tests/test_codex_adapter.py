import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from esc_exec.codex_adapter import CodexAdapter, CodexError, codex_auth_status, sandbox_for_policy
from esc_exec.contracts import validate_contract
from esc_exec.model import ManifestState
from esc_exec.registry import add_route
from esc_exec.indexing import generate_indexes
from esc_exec.roadmap import save_project_roadmap
from esc_exec.manifests import (
    component_manifest_path, component_manifest_relative_path, repository_manifest_path,
)
from esc_exec.yaml_io import write_yaml


def _event_stream(*, thread_id="thread-created", summary="The content component owns lesson publishing.", command_exit_code=0, include_error=False):
    """Reconstructs the real event shape observed from a live `codex -a never exec
    -s read-only --skip-git-repo-check --json` run (verified 2026-07-19, codex-cli
    0.144.1) -- not a guessed schema."""
    messages = [
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "turn.started"},
        {"type": "item.started", "item": {"id": "item_0", "type": "command_execution", "command": "/bin/bash -lc 'cat esc-index.json'", "aggregated_output": "", "exit_code": None, "status": "in_progress"}},
        {"type": "item.completed", "item": {"id": "item_0", "type": "command_execution", "command": "/bin/bash -lc 'cat esc-index.json'", "aggregated_output": "index contents", "exit_code": command_exit_code, "status": "completed"}},
    ]
    if include_error:
        messages.append({"type": "error", "message": "model unavailable"})
    else:
        messages.append({"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": summary}})
        messages.append({"type": "turn.completed", "usage": {"input_tokens": 120, "cached_input_tokens": 40, "output_tokens": 30, "reasoning_output_tokens": 5}})
    return messages


class FakeCodexClient:
    def __init__(self, messages=None):
        self.messages = messages if messages is not None else _event_stream()
        self.prompts: list[str] = []
        self.sandboxes: list[str] = []

    def run(self, directory, prompt, sandbox, model=None):
        self.prompts.append(prompt)
        self.sandboxes.append(sandbox)
        return self.messages


class FakeNoOutcomeClient(FakeCodexClient):
    def __init__(self):
        super().__init__(messages=[{"type": "thread.started", "thread_id": "thread-created"}])


class FakeRaisingClient(FakeCodexClient):
    def run(self, directory, prompt, sandbox, model=None):
        raise CodexError("codex exec exited 1: rate limited")


class CodexAdapterTests(unittest.TestCase):
    @staticmethod
    def _repository(root: Path) -> Path:
        repository = root / "repo"
        (repository / "content").mkdir(parents=True)
        write_yaml(repository_manifest_path(repository), {
            "schema_version": 1,
            "repository": {"id": "ampm-backend", "type": "gradle-multi-project", "purpose": "test"},
            "components": [{"id": "content", "manifest": component_manifest_relative_path("content")}],
        })
        write_yaml(component_manifest_path(repository, "content"), {
            "schema_version": 1,
            "component": {"id": "content", "type": "gradle-module", "path": "content", "purpose": "Owns lesson publishing."},
            "build": {"system": "gradle", "project": ":content"},
            "paths": {"source": "src/main/kotlin"},
        })
        generate_indexes(repository)
        return repository

    def test_execute_emits_valid_portable_contracts(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository = self._repository(root)
            add_route(registry, "repositories", "ampm-backend", repository)
            client = FakeCodexClient()
            run_dir = CodexAdapter(client, registry).execute(
                examples / "task.yaml", examples / "workspace.yaml",
                examples / "adapter-codex.yaml", examples / "policy.yaml",
            )
            self.assertEqual(ManifestState.VALID, validate_contract("run", run_dir / "run.json").state)
            self.assertEqual(ManifestState.VALID, validate_contract("event", run_dir / "events.jsonl").state)
            self.assertEqual(ManifestState.VALID, validate_contract("artifact", run_dir / "artifact.json").state)
            self.assertEqual(ManifestState.VALID, validate_contract("task-context", run_dir / "task-context.json").state)
            self.assertEqual(ManifestState.VALID, validate_contract("run-metrics", run_dir / "run-metrics.json").state)
            run = json.loads((run_dir / "run.json").read_text())
            self.assertEqual("thread-created", run["adapter_metadata"]["thread_id"])
            self.assertEqual("read-only", run["bindings"]["sandbox"])
            self.assertIn("content/esc-index.json", client.prompts[0])
            self.assertIn("read-only", client.prompts[0])
            metrics = json.loads((run_dir / "run-metrics.json").read_text())
            self.assertEqual(155, metrics["tokens"]["total"])  # 120 input + 30 output + 5 reasoning
            self.assertEqual(1, metrics["execution"]["tool_calls"])

    def test_project_roadmap_reaches_the_real_prompt(self):
        """plan/done/project-vision-and-direction.md design 2: parity across
        every adapter, not just the one this was first built against."""
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository = self._repository(root)
            add_route(registry, "repositories", "ampm-backend", repository)
            save_project_roadmap(repository, "ampm-backend", "A lesson-publishing app.", "Core flow built.", "Adding review next.")
            client = FakeCodexClient()
            CodexAdapter(client, registry).execute(
                examples / "task.yaml", examples / "workspace.yaml", examples / "adapter-codex.yaml", examples / "policy.yaml",
            )
            self.assertIn("A lesson-publishing app.", client.prompts[0])
            self.assertIn("Adding review next.", client.prompts[0])

    def test_architecture_documents_reach_the_real_prompt(self):
        """plan/active/architecture-guidance-prompt-delivery.md design 5: parity
        across every adapter, not just the one this was first built against."""
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository = self._repository(root)
            write_yaml(component_manifest_path(repository, "content"), {
                "schema_version": 1,
                "component": {"id": "content", "type": "gradle-module", "path": "content", "purpose": "Owns lesson publishing."},
                "build": {"system": "gradle", "project": ":content"},
                "paths": {"source": "src/main/kotlin"},
                "architecture": {"profile_ids": ["ORCH-BE-FEAT"]},
            })
            generate_indexes(repository)
            add_route(registry, "repositories", "ampm-backend", repository)
            framework_root = root / "architecture-framework"
            framework_root.mkdir()
            (framework_root / "index.json").write_text(json.dumps({
                "generated": "2026-01-01T00:00:00Z", "count": 1,
                "documents": [{
                    "id": "ORCH-BE-FEAT", "path": "feature-orchestrators/backend/feature.md",
                    "layer": "feature-orchestrators", "requires": [], "status": "active",
                }],
            }), encoding="utf-8")
            add_route(registry, "frameworks", "esc-ai-architecture-framework", framework_root)
            client = FakeCodexClient()
            CodexAdapter(client, registry).execute(
                examples / "task.yaml", examples / "workspace.yaml", examples / "adapter-codex.yaml", examples / "policy.yaml",
            )
            self.assertIn("feature-orchestrators/backend/feature.md", client.prompts[0])
            self.assertIn("ORCH-BE-FEAT", client.prompts[0])

    def test_explicit_error_event_fails_the_run(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            add_route(registry, "repositories", "ampm-backend", self._repository(root))
            client = FakeCodexClient(messages=_event_stream(include_error=True))
            with self.assertRaises(CodexError):
                CodexAdapter(client, registry).execute(
                    examples / "task.yaml", examples / "workspace.yaml",
                    examples / "adapter-codex.yaml", examples / "policy.yaml",
                )
            run_dir = next((root / "repo" / ".esc-ai" / "runs").iterdir())
            run = json.loads((run_dir / "run.json").read_text())
            self.assertEqual("failed", run["run"]["status"])

    def test_missing_agent_message_fails_without_inventing_tokens(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            add_route(registry, "repositories", "ampm-backend", self._repository(root))
            with self.assertRaises(CodexError):
                CodexAdapter(FakeNoOutcomeClient(), registry).execute(
                    examples / "task.yaml", examples / "workspace.yaml",
                    examples / "adapter-codex.yaml", examples / "policy.yaml",
                )
            run_dir = next((root / "repo" / ".esc-ai" / "runs").iterdir())
            metrics = json.loads((run_dir / "run-metrics.json").read_text())
            self.assertEqual("failed", metrics["run"]["status"])
            self.assertEqual("unavailable", metrics["tokens"]["status"])
            self.assertIsNone(metrics["tokens"]["total"])
            self.assertEqual(ManifestState.VALID, validate_contract("run-metrics", run_dir / "run-metrics.json").state)

    def test_subprocess_failure_is_surfaced_as_codex_error(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            add_route(registry, "repositories", "ampm-backend", self._repository(root))
            with self.assertRaises(CodexError):
                CodexAdapter(FakeRaisingClient(), registry).execute(
                    examples / "task.yaml", examples / "workspace.yaml",
                    examples / "adapter-codex.yaml", examples / "policy.yaml",
                )

    def test_wrong_provider_is_rejected(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            add_route(registry, "repositories", "ampm-backend", self._repository(root))
            with self.assertRaises(ValueError):
                CodexAdapter(FakeCodexClient(), registry).execute(
                    examples / "task.yaml", examples / "workspace.yaml",
                    examples / "adapter.yaml", examples / "policy.yaml",  # opencode adapter, wrong provider
                )

    def test_readonly_policy_selects_read_only_sandbox(self):
        self.assertEqual("read-only", sandbox_for_policy({
            "permissions": {"read": "allow", "edit": "deny", "execute": "deny", "network": "deny"},
        }))

    def test_edit_allow_selects_workspace_write(self):
        self.assertEqual("workspace-write", sandbox_for_policy({"permissions": {"edit": "allow"}}))

    def test_execute_allow_selects_workspace_write(self):
        self.assertEqual("workspace-write", sandbox_for_policy({"permissions": {"execute": "allow"}}))

    def test_edit_and_execute_cannot_be_split_apart(self):
        # sandbox_for_policy's documented limitation: allowing only `execute` still
        # yields the same workspace-write sandbox as allowing only `edit` -- Codex has
        # no sandbox mode that permits one without the other.
        edit_only = sandbox_for_policy({"permissions": {"edit": "allow", "execute": "deny"}})
        execute_only = sandbox_for_policy({"permissions": {"edit": "deny", "execute": "allow"}})
        self.assertEqual(edit_only, execute_only)

    def test_missing_permissions_key_is_read_only(self):
        self.assertEqual("read-only", sandbox_for_policy({"permissions": {}}))

    def test_execute_wires_actual_sandbox_into_request_and_run_bindings(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository = self._repository(root)
            add_route(registry, "repositories", "ampm-backend", repository)
            policy_path = root / "edit-policy.yaml"
            write_yaml(policy_path, {
                "schema_version": 1,
                "policy": {"id": "edit-allowed", "description": "Permit edits for this test."},
                "permissions": {"read": "allow", "edit": "allow", "execute": "deny", "network": "deny"},
            })
            client = FakeCodexClient()
            run_dir = CodexAdapter(client, registry).execute(
                examples / "task.yaml", examples / "workspace.yaml", examples / "adapter-codex.yaml", policy_path,
            )
            self.assertEqual("workspace-write", client.sandboxes[0])
            self.assertNotIn("read-only", client.prompts[0])
            self.assertIn("You may edit files", client.prompts[0])
            run = json.loads((run_dir / "run.json").read_text())
            self.assertEqual("workspace-write", run["bindings"]["sandbox"])
            self.assertEqual(ManifestState.VALID, validate_contract("run", run_dir / "run.json").state)

    def test_instruction_bundle_is_written_and_referenced_from_run_json(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository = self._repository(root)
            add_route(registry, "repositories", "ampm-backend", repository)
            run_dir = CodexAdapter(FakeCodexClient(), registry).execute(
                examples / "task.yaml", examples / "workspace.yaml", examples / "adapter-codex.yaml", examples / "policy.yaml",
            )
            bundle_path = run_dir / "instruction-bundle.json"
            self.assertTrue(bundle_path.is_file())
            document = json.loads(bundle_path.read_text())
            levels = [entry["level"] for entry in document["levels"]]
            self.assertIn("safety_and_operator_policy", levels)
            run = json.loads((run_dir / "run.json").read_text())
            self.assertEqual("instruction-bundle.json", run["bindings"]["instruction_bundle"])

    def test_tool_events_report_command_execution_status(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository = self._repository(root)
            add_route(registry, "repositories", "ampm-backend", repository)
            client = FakeCodexClient(messages=_event_stream(command_exit_code=1))
            run_dir = CodexAdapter(client, registry).execute(
                examples / "task.yaml", examples / "workspace.yaml", examples / "adapter-codex.yaml", examples / "policy.yaml",
            )
            events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
            tool_events = [event["event"] for event in events if event["event"]["type"] == "tool.completed"]
            self.assertEqual(1, len(tool_events))
            self.assertEqual("error", tool_events[0]["payload"]["status"])
            self.assertEqual("command_execution", tool_events[0]["payload"]["tool"])


class CodexAuthStatusTests(unittest.TestCase):
    def test_returns_raw_text_from_stdout_on_success(self):
        fake_result = MagicMock(returncode=0, stdout="Logged in using ChatGPT\n", stderr="")
        with patch("esc_exec.codex_adapter.subprocess.run", return_value=fake_result):
            self.assertEqual("Logged in using ChatGPT", codex_auth_status())

    def test_falls_back_to_stderr_when_stdout_is_empty(self):
        # Regression test for a real bug caught by live-testing the connect flow:
        # `codex login status` actually prints to stderr, not stdout -- reading only
        # stdout silently reported "not logged in" while genuinely logged in.
        fake_result = MagicMock(returncode=0, stdout="", stderr="Logged in using ChatGPT\n")
        with patch("esc_exec.codex_adapter.subprocess.run", return_value=fake_result):
            self.assertEqual("Logged in using ChatGPT", codex_auth_status())

    def test_nonzero_exit_returns_none(self):
        fake_result = MagicMock(returncode=1, stdout="", stderr="")
        with patch("esc_exec.codex_adapter.subprocess.run", return_value=fake_result):
            self.assertIsNone(codex_auth_status())

    def test_missing_binary_returns_none(self):
        with patch("esc_exec.codex_adapter.subprocess.run", side_effect=FileNotFoundError):
            self.assertIsNone(codex_auth_status())


if __name__ == "__main__":
    unittest.main()
