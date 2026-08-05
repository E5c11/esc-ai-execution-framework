import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.contracts import validate_contract
from esc_exec.model import ManifestState
from esc_exec.opencode_adapter import OpenCodeAdapter, OpenCodeError, tools_for_policy
from esc_exec.registry import add_route
from esc_exec.indexing import generate_indexes
from esc_exec.manifests import (
    component_manifest_path, component_manifest_relative_path, repository_manifest_path,
)
from esc_exec.roadmap import save_project_roadmap
from esc_exec.yaml_io import write_yaml


class FakeOpenCodeClient:
    base_url = "http://fake"
    prompts: list[str]
    tool_grants: list[dict]

    def __init__(self): self.prompts, self.tool_grants = [], []
    def health(self, directory): return {"worktree": str(directory)}
    def create_session(self, directory, title): return {"id": "ses-created", "title": title}
    def fork(self, directory, session_id): return {"id": "ses-forked", "parentID": session_id}
    def prompt(self, directory, session_id, prompt, tools, model=None):
        self.prompts.append(prompt)
        self.tool_grants.append(tools)
        return {"parts": [
            {"type": "tool", "tool": "read", "state": {"status": "completed", "title": "Read index"}},
            {"type": "text", "text": "The content component owns lesson publishing."},
        ], "info": {"tokens": {"input": 120, "output": 30, "reasoning": 5, "cache": {"read": 40, "write": 2}}}}


class FakeErrorClient(FakeOpenCodeClient):
    def prompt(self, directory, session_id, prompt, tools, model=None):
        return {"parts": [], "info": {"error": "model unavailable"}}


class OpenCodeAdapterTests(unittest.TestCase):
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

    def test_project_roadmap_reaches_the_real_prompt(self):
        """plan/done/project-vision-and-direction.md design 2: parity with the
        Claude Code adapter -- every provider must see a saved roadmap, not just
        the first one this was built against."""
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository = self._repository(root)
            add_route(registry, "repositories", "ampm-backend", repository)
            save_project_roadmap(repository, "ampm-backend", "A lesson-publishing app.", "Core flow built.", "Adding review next.")
            client = FakeOpenCodeClient()
            OpenCodeAdapter(client, registry).execute(
                examples / "task.yaml", examples / "workspace.yaml", examples / "adapter.yaml", examples / "policy.yaml",
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
            client = FakeOpenCodeClient()
            OpenCodeAdapter(client, registry).execute(
                examples / "task.yaml", examples / "workspace.yaml", examples / "adapter.yaml", examples / "policy.yaml",
            )
            self.assertIn("feature-orchestrators/backend/feature.md", client.prompts[0])
            self.assertIn("ORCH-BE-FEAT", client.prompts[0])

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

    def test_readonly_example_policy_grants_only_read_tools(self):
        # examples/contracts/policy.yaml ("readonly-review") declares read: allow,
        # edit: deny, execute: ask, network: deny -- this must compute to exactly
        # today's historical hardcoded grant, so the read-only spike behaves
        # identically to before this fix.
        grant = tools_for_policy({
            "permissions": {"read": "allow", "edit": "deny", "execute": "ask", "network": "deny", "external_paths": "deny"},
        })
        self.assertEqual({
            "read": True, "list": True, "glob": True, "grep": True,
            "bash": False, "edit": False, "write": False, "patch": False, "webfetch": False,
            "task": False, "todowrite": False, "todoread": False,
        }, grant)

    def test_edit_allow_grants_edit_write_patch(self):
        grant = tools_for_policy({"permissions": {"edit": "allow"}})
        self.assertTrue(grant["edit"] and grant["write"] and grant["patch"])
        self.assertFalse(grant["bash"] or grant["webfetch"] or grant["read"])

    def test_execute_allow_grants_bash_only(self):
        grant = tools_for_policy({"permissions": {"execute": "allow"}})
        self.assertTrue(grant["bash"])
        self.assertFalse(grant["edit"] or grant["write"] or grant["patch"] or grant["webfetch"])

    def test_network_allow_grants_webfetch_only(self):
        grant = tools_for_policy({"permissions": {"network": "allow"}})
        self.assertTrue(grant["webfetch"])
        self.assertFalse(grant["bash"] or grant["edit"])

    def test_ask_permission_is_treated_as_denied(self):
        grant = tools_for_policy({"permissions": {"execute": "ask", "edit": "ask", "network": "ask"}})
        self.assertFalse(grant["bash"] or grant["edit"] or grant["write"] or grant["patch"] or grant["webfetch"])

    def test_missing_permissions_key_denies_everything(self):
        grant = tools_for_policy({"permissions": {}})
        self.assertFalse(any(grant.values()))

    def test_unmapped_categories_stay_denied(self):
        # task/todowrite/todoread aren't covered by any policy.schema.yaml
        # permission category -- they must never be grantable through this mapping.
        grant = tools_for_policy({"permissions": {"read": "allow", "edit": "allow", "execute": "allow", "network": "allow"}})
        self.assertFalse(grant["task"] or grant["todowrite"] or grant["todoread"])

    def test_execute_wires_actual_tool_grant_into_request_and_run_bindings(self):
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
            client = FakeOpenCodeClient()
            run_dir = OpenCodeAdapter(client, registry).execute(
                examples / "task.yaml", examples / "workspace.yaml", examples / "adapter.yaml", policy_path,
            )
            expected_grant = {
                "read": True, "list": True, "glob": True, "grep": True,
                "bash": False, "edit": True, "write": True, "patch": True, "webfetch": False,
                "task": False, "todowrite": False, "todoread": False,
            }
            self.assertEqual(expected_grant, client.tool_grants[0])
            self.assertNotIn("read-only", client.prompts[0])
            self.assertIn("You may edit files.", client.prompts[0])
            run = json.loads((run_dir / "run.json").read_text())
            self.assertEqual(expected_grant, run["bindings"]["tool_grant"])
            self.assertEqual(ManifestState.VALID, validate_contract("run", run_dir / "run.json").state)

    def test_instruction_bundle_is_written_ordered_and_referenced_from_run_json(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository = self._repository(root)
            add_route(registry, "repositories", "ampm-backend", repository)
            run_dir = OpenCodeAdapter(FakeOpenCodeClient(), registry).execute(
                examples / "task.yaml", examples / "workspace.yaml", examples / "adapter.yaml", examples / "policy.yaml",
            )
            bundle_path = run_dir / "instruction-bundle.json"
            self.assertTrue(bundle_path.is_file())
            document = json.loads(bundle_path.read_text())
            self.assertEqual(1, document["schema_version"])
            levels = [entry["level"] for entry in document["levels"]]
            # PRECEDENCE order must be respected; only non-empty levels appear.
            self.assertEqual(sorted(levels, key=levels.index), levels)
            self.assertIn("safety_and_operator_policy", levels)
            self.assertIn("component_manifests_and_profiles", levels)
            self.assertIn("active_workflow_task_specification", levels)
            safety = next(entry for entry in document["levels"] if entry["level"] == "safety_and_operator_policy")
            self.assertEqual(["policy:readonly-review"], safety["sources"])
            run = json.loads((run_dir / "run.json").read_text())
            self.assertEqual("instruction-bundle.json", run["bindings"]["instruction_bundle"])

    def test_instruction_bundle_includes_workflow_policy_extension_reference(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository = self._repository(root)
            workflow_readme = repository / ".esc-ai" / "workflows" / "README.md"
            workflow_readme.parent.mkdir(parents=True)
            workflow_readme.write_text(
                "---\nschema_version: 1\npolicy:\n  extension:\n    id: ampm-backend-framework\n    precedence: overrides core for backend rules\n---\n\n# Workflows\n",
                encoding="utf-8",
            )
            add_route(registry, "repositories", "ampm-backend", repository)
            run_dir = OpenCodeAdapter(FakeOpenCodeClient(), registry).execute(
                examples / "task.yaml", examples / "workspace.yaml", examples / "adapter.yaml", examples / "policy.yaml",
            )
            document = json.loads((run_dir / "instruction-bundle.json").read_text())
            workflow_level = next(
                entry for entry in document["levels"] if entry["level"] == "repository_instructions_and_workflow_policy"
            )
            self.assertIn(".esc-ai/workflows/README.md", workflow_level["sources"])
            self.assertIn("extension:ampm-backend-framework", workflow_level["sources"])


if __name__ == "__main__":
    unittest.main()
