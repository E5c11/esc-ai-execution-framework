import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.contracts import validate_contract
from esc_exec.model import ManifestState
from esc_exec.opencode_adapter import OpenCodeAdapter, OpenCodeError
from esc_exec.registry import add_route
from esc_exec.indexing import generate_indexes
from esc_exec.yaml_io import write_yaml


class FakeOpenCodeClient:
    base_url = "http://fake"
    prompts: list[str]

    def __init__(self): self.prompts = []
    def health(self, directory): return {"worktree": str(directory)}
    def create_session(self, directory, title): return {"id": "ses-created", "title": title}
    def fork(self, directory, session_id): return {"id": "ses-forked", "parentID": session_id}
    def prompt(self, directory, session_id, prompt, model=None):
        self.prompts.append(prompt)
        return {"parts": [
            {"type": "tool", "tool": "read", "state": {"status": "completed", "title": "Read index"}},
            {"type": "text", "text": "The content component owns lesson publishing."},
        ], "info": {"tokens": {"input": 120, "output": 30, "reasoning": 5, "cache": {"read": 40, "write": 2}}}}


class FakeErrorClient(FakeOpenCodeClient):
    def prompt(self, directory, session_id, prompt, model=None):
        return {"parts": [], "info": {"error": "model unavailable"}}


class OpenCodeAdapterTests(unittest.TestCase):
    @staticmethod
    def _repository(root: Path) -> Path:
        repository = root / "repo"
        (repository / "content").mkdir(parents=True)
        write_yaml(repository / "esc-execution.yaml", {
            "schema_version": 1,
            "repository": {"id": "ampm-backend", "type": "gradle-multi-project", "purpose": "test"},
            "components": [{"id": "content", "manifest": "content/esc-component.yaml"}],
        })
        write_yaml(repository / "content/esc-component.yaml", {
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
            client = FakeOpenCodeClient()
            run_dir = OpenCodeAdapter(client, registry).execute(
                examples / "task.yaml", examples / "workspace.yaml",
                examples / "adapter.yaml", examples / "policy.yaml",
            )
            self.assertEqual(ManifestState.VALID, validate_contract("run", run_dir / "run.json").state)
            self.assertEqual(ManifestState.VALID, validate_contract("event", run_dir / "events.jsonl").state)
            self.assertEqual(ManifestState.VALID, validate_contract("artifact", run_dir / "artifact.json").state)
            self.assertEqual(ManifestState.VALID, validate_contract("task-context", run_dir / "task-context.json").state)
            self.assertEqual(ManifestState.VALID, validate_contract("run-metrics", run_dir / "run-metrics.json").state)
            run = json.loads((run_dir / "run.json").read_text())
            self.assertEqual("ses-created", run["adapter_metadata"]["session_id"])
            self.assertIn("content/esc-index.json", client.prompts[0])
            self.assertIn("read-only", client.prompts[0])
            metrics = json.loads((run_dir / "run-metrics.json").read_text())
            self.assertEqual(155, metrics["tokens"]["total"])
            self.assertEqual(1, metrics["execution"]["tool_calls"])

    def test_fork_returns_provider_session_id(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            add_route(registry, "repositories", "ampm-backend", self._repository(root))
            result = OpenCodeAdapter(FakeOpenCodeClient(), registry).fork("ampm-backend", "ses-parent")
            self.assertEqual("ses-forked", result)

    def test_failed_run_retains_metrics_without_inventing_tokens(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            add_route(registry, "repositories", "ampm-backend", self._repository(root))
            with self.assertRaises(OpenCodeError):
                OpenCodeAdapter(FakeErrorClient(), registry).execute(
                    examples / "task.yaml", examples / "workspace.yaml",
                    examples / "adapter.yaml", examples / "policy.yaml",
                )
            run_dir = next((root / "repo" / ".esc-ai" / "runs").iterdir())
            metrics = json.loads((run_dir / "run-metrics.json").read_text())
            self.assertEqual("failed", metrics["run"]["status"])
            self.assertEqual("unavailable", metrics["tokens"]["status"])
            self.assertIsNone(metrics["tokens"]["total"])
            self.assertEqual(ManifestState.VALID, validate_contract("run-metrics", run_dir / "run-metrics.json").state)


if __name__ == "__main__":
    unittest.main()
