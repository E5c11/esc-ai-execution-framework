import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from esc_exec.claude_code_adapter import (
    ClaudeCodeAdapter, ClaudeCodeClient, ClaudeCodeError, _extract_architecture_style, _suggest_groundable_answers,
    claude_auth_status, granted_categories, suggest_architecture_coverage_gap, suggest_onboarding_answers,
    suggest_work_type_drift, tools_for_policy,
)
from esc_exec.contracts import validate_contract
from esc_exec.model import ManifestState
from esc_exec.registry import add_route
from esc_exec.indexing import generate_indexes
from esc_exec.manifests import (
    component_manifest_path, component_manifest_relative_path, repository_manifest_path,
)
from esc_exec.roadmap import save_project_roadmap
from esc_exec.yaml_io import load_yaml, write_yaml


def _stream_json(*, session_id="ses-created", tool_use_id="toolu-1", summary="The content component owns lesson publishing.", is_error=False):
    """Reconstructs the real message shape observed from a live `claude -p
    --output-format stream-json --verbose` run (verified 2026-07-19, claude-code
    2.1.215) -- not a guessed schema."""
    return [
        {"type": "system", "subtype": "init", "session_id": session_id, "tools": ["Read"]},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": tool_use_id, "name": "Read", "input": {"file_path": "content/esc-index.json"}},
        ]}, "session_id": session_id},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tool_use_id, "content": "index contents"},
        ]}, "session_id": session_id},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": summary}]}, "session_id": session_id},
        {
            "type": "result", "subtype": "success", "is_error": is_error, "result": summary if not is_error else "model unavailable",
            "session_id": session_id, "num_turns": 2, "total_cost_usd": 0.0123,
            "usage": {"input_tokens": 120, "output_tokens": 30, "cache_read_input_tokens": 40, "cache_creation_input_tokens": 2},
        },
    ]


class FakeClaudeCodeClient:
    def __init__(self, messages=None):
        self.messages = messages if messages is not None else _stream_json()
        self.directories: list[Path] = []
        self.prompts: list[str] = []
        self.tool_grants: list[list[str]] = []
        self.resume_ids: list[str | None] = []

    def run(self, directory, prompt, tools, model=None, resume_session_id=None):
        self.directories.append(directory)
        self.prompts.append(prompt)
        self.tool_grants.append(tools)
        self.resume_ids.append(resume_session_id)
        return self.messages


class FakeNoResultClient(FakeClaudeCodeClient):
    def __init__(self):
        super().__init__(messages=[{"type": "system", "subtype": "init", "session_id": "ses-created"}])


class FakeRaisingClient(FakeClaudeCodeClient):
    def run(self, directory, prompt, tools, model=None, resume_session_id=None):
        raise ClaudeCodeError("claude -p exited 1: rate limited")


class ClaudeCodeAdapterTests(unittest.TestCase):
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
            client = FakeClaudeCodeClient()
            run_dir = ClaudeCodeAdapter(client, registry).execute(
                examples / "task.yaml", examples / "workspace.yaml",
                examples / "adapter-claude-code.yaml", examples / "policy.yaml",
            )
            self.assertEqual(ManifestState.VALID, validate_contract("run", run_dir / "run.json").state)
            self.assertEqual(ManifestState.VALID, validate_contract("event", run_dir / "events.jsonl").state)
            self.assertEqual(ManifestState.VALID, validate_contract("artifact", run_dir / "artifact.json").state)
            self.assertEqual(ManifestState.VALID, validate_contract("task-context", run_dir / "task-context.json").state)
            self.assertEqual(ManifestState.VALID, validate_contract("run-metrics", run_dir / "run-metrics.json").state)
            run = json.loads((run_dir / "run.json").read_text())
            self.assertEqual("ses-created", run["adapter_metadata"]["session_id"])
            self.assertEqual(0.0123, run["adapter_metadata"]["total_cost_usd"])
            self.assertIn("content/esc-index.json", client.prompts[0])
            self.assertIn("read-only", client.prompts[0])
            metrics = json.loads((run_dir / "run-metrics.json").read_text())
            self.assertEqual(150, metrics["tokens"]["total"])  # 120 input + 30 output + 0 reasoning
            self.assertEqual(1, metrics["execution"]["tool_calls"])
            self.assertEqual(1, metrics["execution"]["read_calls"])  # "Read" matched case-insensitively

    def test_result_is_error_true_fails_the_run(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            add_route(registry, "repositories", "ampm-backend", self._repository(root))
            client = FakeClaudeCodeClient(messages=_stream_json(is_error=True))
            with self.assertRaises(ClaudeCodeError):
                ClaudeCodeAdapter(client, registry).execute(
                    examples / "task.yaml", examples / "workspace.yaml",
                    examples / "adapter-claude-code.yaml", examples / "policy.yaml",
                )
            run_dir = next((root / "repo" / ".esc-ai" / "runs").iterdir())
            run = json.loads((run_dir / "run.json").read_text())
            self.assertEqual("failed", run["run"]["status"])

    def test_missing_result_message_fails_without_inventing_tokens(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            add_route(registry, "repositories", "ampm-backend", self._repository(root))
            with self.assertRaises(ClaudeCodeError):
                ClaudeCodeAdapter(FakeNoResultClient(), registry).execute(
                    examples / "task.yaml", examples / "workspace.yaml",
                    examples / "adapter-claude-code.yaml", examples / "policy.yaml",
                )
            run_dir = next((root / "repo" / ".esc-ai" / "runs").iterdir())
            metrics = json.loads((run_dir / "run-metrics.json").read_text())
            self.assertEqual("failed", metrics["run"]["status"])
            self.assertEqual("unavailable", metrics["tokens"]["status"])
            self.assertIsNone(metrics["tokens"]["total"])
            self.assertEqual(ManifestState.VALID, validate_contract("run-metrics", run_dir / "run-metrics.json").state)

    def test_subprocess_failure_is_surfaced_as_claude_code_error(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            add_route(registry, "repositories", "ampm-backend", self._repository(root))
            with self.assertRaises(ClaudeCodeError):
                ClaudeCodeAdapter(FakeRaisingClient(), registry).execute(
                    examples / "task.yaml", examples / "workspace.yaml",
                    examples / "adapter-claude-code.yaml", examples / "policy.yaml",
                )

    def test_session_id_is_passed_through_as_resume(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            add_route(registry, "repositories", "ampm-backend", self._repository(root))
            client = FakeClaudeCodeClient()
            ClaudeCodeAdapter(client, registry).execute(
                examples / "task.yaml", examples / "workspace.yaml",
                examples / "adapter-claude-code.yaml", examples / "policy.yaml",
                session_id="ses-prior",
            )
            self.assertEqual(["ses-prior"], client.resume_ids)

    def test_wrong_provider_is_rejected(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            add_route(registry, "repositories", "ampm-backend", self._repository(root))
            with self.assertRaises(ValueError):
                ClaudeCodeAdapter(FakeClaudeCodeClient(), registry).execute(
                    examples / "task.yaml", examples / "workspace.yaml",
                    examples / "adapter.yaml", examples / "policy.yaml",  # opencode adapter, wrong provider
                )

    def test_readonly_example_policy_grants_only_read_tools(self):
        grant = tools_for_policy({
            "permissions": {"read": "allow", "edit": "deny", "execute": "ask", "network": "deny", "external_paths": "deny"},
        })
        self.assertEqual(["Read", "Glob", "Grep"], grant)

    def test_edit_allow_grants_edit_write_notebookedit(self):
        grant = tools_for_policy({"permissions": {"edit": "allow"}})
        self.assertEqual(["Edit", "Write", "NotebookEdit"], grant)

    def test_execute_allow_grants_bash_only(self):
        grant = tools_for_policy({"permissions": {"execute": "allow"}})
        self.assertEqual(["Bash"], grant)

    def test_network_allow_grants_webfetch_and_websearch(self):
        grant = tools_for_policy({"permissions": {"network": "allow"}})
        self.assertEqual(["WebFetch", "WebSearch"], grant)

    def test_ask_permission_is_treated_as_denied(self):
        grant = tools_for_policy({"permissions": {"execute": "ask", "edit": "ask", "network": "ask"}})
        self.assertEqual([], grant)

    def test_missing_permissions_key_denies_everything(self):
        self.assertEqual([], tools_for_policy({"permissions": {}}))

    def test_granted_categories_lists_only_allowed_categories(self):
        self.assertEqual(
            ["read", "edit"],
            granted_categories({"permissions": {"read": "allow", "edit": "allow", "execute": "deny", "network": "ask"}}),
        )

    def test_granted_categories_empty_for_missing_permissions(self):
        self.assertEqual([], granted_categories({"permissions": {}}))

    def test_run_json_records_consent_binding(self):
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
            run_dir = ClaudeCodeAdapter(FakeClaudeCodeClient(), registry).execute(
                examples / "task.yaml", examples / "workspace.yaml", examples / "adapter-claude-code.yaml", policy_path,
            )
            run = json.loads((run_dir / "run.json").read_text())
            consent = run["bindings"]["consent"]
            self.assertEqual(["read", "edit"], consent["granted_categories"])
            self.assertTrue(consent["granted_at"])

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
            client = FakeClaudeCodeClient()
            run_dir = ClaudeCodeAdapter(client, registry).execute(
                examples / "task.yaml", examples / "workspace.yaml", examples / "adapter-claude-code.yaml", policy_path,
            )
            expected_grant = ["Read", "Glob", "Grep", "Edit", "Write", "NotebookEdit"]
            self.assertEqual(expected_grant, client.tool_grants[0])
            self.assertNotIn("read-only", client.prompts[0])
            self.assertIn("You may edit files.", client.prompts[0])
            run = json.loads((run_dir / "run.json").read_text())
            self.assertEqual(expected_grant, run["bindings"]["tool_grant"])
            self.assertEqual(ManifestState.VALID, validate_contract("run", run_dir / "run.json").state)

    def test_instruction_bundle_is_written_and_referenced_from_run_json(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository = self._repository(root)
            add_route(registry, "repositories", "ampm-backend", repository)
            run_dir = ClaudeCodeAdapter(FakeClaudeCodeClient(), registry).execute(
                examples / "task.yaml", examples / "workspace.yaml", examples / "adapter-claude-code.yaml", examples / "policy.yaml",
            )
            bundle_path = run_dir / "instruction-bundle.json"
            self.assertTrue(bundle_path.is_file())
            document = json.loads(bundle_path.read_text())
            levels = [entry["level"] for entry in document["levels"]]
            self.assertIn("safety_and_operator_policy", levels)
            run = json.loads((run_dir / "run.json").read_text())
            self.assertEqual("instruction-bundle.json", run["bindings"]["instruction_bundle"])

    def test_project_roadmap_reaches_the_real_prompt_and_the_bundle(self):
        """plan/done/project-vision-and-direction.md design 2: a saved
        project_roadmap must reach the actual text sent to the agent, not just an
        unread instruction-bundle.json entry."""
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository = self._repository(root)
            add_route(registry, "repositories", "ampm-backend", repository)
            save_project_roadmap(
                repository, "ampm-backend", "A lesson-publishing app.",
                "Core publishing flow built.", "Adding a review workflow next.",
            )
            client = FakeClaudeCodeClient()
            run_dir = ClaudeCodeAdapter(client, registry).execute(
                examples / "task.yaml", examples / "workspace.yaml", examples / "adapter-claude-code.yaml", examples / "policy.yaml",
            )
            self.assertIn("A lesson-publishing app.", client.prompts[0])
            self.assertIn("Adding a review workflow next.", client.prompts[0])
            bundle = json.loads((run_dir / "instruction-bundle.json").read_text())
            workflow_level = next(entry for entry in bundle["levels"] if entry["level"] == "repository_instructions_and_workflow_policy")
            self.assertIn(".esc-ai/roadmap.yaml", workflow_level["sources"])

    def test_no_project_roadmap_omits_it_from_prompt_and_bundle(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository = self._repository(root)
            add_route(registry, "repositories", "ampm-backend", repository)
            client = FakeClaudeCodeClient()
            run_dir = ClaudeCodeAdapter(client, registry).execute(
                examples / "task.yaml", examples / "workspace.yaml", examples / "adapter-claude-code.yaml", examples / "policy.yaml",
            )
            self.assertNotIn("Project roadmap", client.prompts[0])
            bundle = json.loads((run_dir / "instruction-bundle.json").read_text())
            levels = {entry["level"]: entry["sources"] for entry in bundle["levels"]}
            sources = levels.get("repository_instructions_and_workflow_policy", [])
            self.assertFalse(any("roadmap" in source for source in sources))


class PermissionDenialsArtifactTests(unittest.TestCase):
    """
    See plan/future/pre-flight-consent-and-bounded-autonomy.md layer 6: a
    permission denial doesn't necessarily set is_error, so it's captured as its
    own artifact rather than folded into run status here -- the Scheduler
    decides what a denial means for the Store-level run status.
    """

    def test_no_denials_writes_empty_denials_list(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            add_route(registry, "repositories", "ampm-backend", ClaudeCodeAdapterTests._repository(root))
            run_dir = ClaudeCodeAdapter(FakeClaudeCodeClient(), registry).execute(
                examples / "task.yaml", examples / "workspace.yaml", examples / "adapter-claude-code.yaml", examples / "policy.yaml",
            )
            document = json.loads((run_dir / "permission-denials.json").read_text())
            self.assertEqual([], document["denials"])

    def test_denials_in_result_message_are_recorded_without_failing_the_run(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        messages = _stream_json()
        messages[-1]["permission_denials"] = [
            {"tool_name": "Bash", "tool_use_id": "toolu-9", "tool_input": {"command": "rm -rf /tmp/x"}},
        ]
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            add_route(registry, "repositories", "ampm-backend", ClaudeCodeAdapterTests._repository(root))
            run_dir = ClaudeCodeAdapter(FakeClaudeCodeClient(messages=messages), registry).execute(
                examples / "task.yaml", examples / "workspace.yaml", examples / "adapter-claude-code.yaml", examples / "policy.yaml",
            )
            run = json.loads((run_dir / "run.json").read_text())
            self.assertEqual("succeeded", run["run"]["status"])  # this adapter's own honest report
            document = json.loads((run_dir / "permission-denials.json").read_text())
            self.assertEqual(1, len(document["denials"]))
            self.assertEqual("Bash", document["denials"][0]["tool_name"])
            self.assertEqual({"command": "rm -rf /tmp/x"}, document["denials"][0]["tool_input"])


class WorktreeIsolationTests(unittest.TestCase):
    """
    See plan/future/pre-flight-consent-and-bounded-autonomy.md layer 4:
    workspace.kind == "worktree" routes the agent's cwd to a disposable git
    worktree instead of the live checkout, and the run finalizes it (commit,
    then keep-if-diff / remove-if-not) afterward.
    """

    @staticmethod
    def _git_repository(root: Path) -> Path:
        repository = ClaudeCodeAdapterTests._repository(root)
        run = lambda *args: subprocess.run(["git", "-C", str(repository), *args], capture_output=True, text=True, check=True)
        run("init", "-q", "-b", "main")
        run("config", "user.email", "test@example.com")
        run("config", "user.name", "Test")
        run("add", "-A")
        run("commit", "-q", "-m", "initial")
        return repository

    @staticmethod
    def _worktree_workspace(root: Path) -> Path:
        path = root / "workspace-worktree.yaml"
        write_yaml(path, {
            "schema_version": 1,
            "workspace": {
                "id": "workspace-ampm-worktree", "kind": "worktree",
                "repository": "ampm-backend", "isolation": "filesystem",
            },
        })
        return path

    def test_agent_runs_inside_a_worktree_not_the_live_checkout(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository = self._git_repository(root)
            add_route(registry, "repositories", "ampm-backend", repository)
            client = FakeClaudeCodeClient()
            ClaudeCodeAdapter(client, registry).execute(
                examples / "task.yaml", self._worktree_workspace(root),
                examples / "adapter-claude-code.yaml", examples / "policy.yaml",
            )
            from esc_exec.worktree import worktree_path
            self.assertEqual(worktree_path(repository, "task-index-review"), client.directories[0])
            self.assertNotEqual(repository, client.directories[0])

    def test_run_with_no_edits_removes_the_worktree_after(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository = self._git_repository(root)
            add_route(registry, "repositories", "ampm-backend", repository)
            run_dir = ClaudeCodeAdapter(FakeClaudeCodeClient(), registry).execute(
                examples / "task.yaml", self._worktree_workspace(root),
                examples / "adapter-claude-code.yaml", examples / "policy.yaml",
            )
            from esc_exec.worktree import worktree_path
            self.assertFalse(worktree_path(repository, "task-index-review").is_dir())
            run = json.loads((run_dir / "run.json").read_text())
            self.assertEqual({"branch": "esc-ai-task-task-index-review", "kept": False}, run["bindings"]["worktree"])

    def test_run_that_edits_files_keeps_the_worktree_for_review(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository = self._git_repository(root)
            add_route(registry, "repositories", "ampm-backend", repository)

            class EditingClient(FakeClaudeCodeClient):
                def run(self, directory, prompt, tools, model=None, resume_session_id=None):
                    (directory / "agent-added-file.txt").write_text("edited\n", encoding="utf-8")
                    return super().run(directory, prompt, tools, model, resume_session_id)

            run_dir = ClaudeCodeAdapter(EditingClient(), registry).execute(
                examples / "task.yaml", self._worktree_workspace(root),
                examples / "adapter-claude-code.yaml", examples / "policy.yaml",
            )
            from esc_exec.worktree import has_uncommitted_changes, worktree_path
            worktree = worktree_path(repository, "task-index-review")
            self.assertTrue(worktree.is_dir())
            self.assertFalse(has_uncommitted_changes(worktree))  # finalize committed it
            run = json.loads((run_dir / "run.json").read_text())
            self.assertEqual({"branch": "esc-ai-task-task-index-review", "kept": True}, run["bindings"]["worktree"])
            self.assertFalse((repository / "agent-added-file.txt").is_file())  # never touched the live checkout

    def test_worktree_inherits_declared_gitignored_files(self):
        """
        plan/active/pre-flight-doctor-and-gate-prerequisites.md: a repository
        manifest's `worktree_inherit` list must be copied into every fresh
        worktree -- a fresh git worktree never contains gitignored local config
        on its own, which otherwise breaks any build that depends on one
        (local.properties-style credentials) on every single task run.
        """
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository = ClaudeCodeAdapterTests._repository(root)
            (repository / ".gitignore").write_text("local.properties\n", encoding="utf-8")
            git = lambda *args: subprocess.run(["git", "-C", str(repository), *args], capture_output=True, text=True, check=True)
            git("init", "-q", "-b", "main")
            git("config", "user.email", "test@example.com")
            git("config", "user.name", "Test")
            git("add", "-A")
            git("commit", "-q", "-m", "initial")
            manifest_path = repository_manifest_path(repository)
            manifest = load_yaml(manifest_path)
            manifest["worktree_inherit"] = ["local.properties"]
            write_yaml(manifest_path, manifest)
            generate_indexes(repository)  # re-index: the manifest edit above changed its digest
            (repository / "local.properties").write_text("sdk.dir=/x\n", encoding="utf-8")
            add_route(registry, "repositories", "ampm-backend", repository)

            class InheritanceCheckingClient(FakeClaudeCodeClient):
                def __init__(self):
                    super().__init__()
                    self.local_properties_content = None

                def run(self, directory, prompt, tools, model=None, resume_session_id=None):
                    # Checked mid-run (before finalize_worktree can remove a
                    # no-diff worktree) -- the only reliable moment the copied
                    # file is guaranteed to still be on disk.
                    properties = directory / "local.properties"
                    self.local_properties_content = properties.read_text(encoding="utf-8") if properties.is_file() else None
                    return super().run(directory, prompt, tools, model, resume_session_id)

            client = InheritanceCheckingClient()
            ClaudeCodeAdapter(client, registry).execute(
                examples / "task.yaml", self._worktree_workspace(root),
                examples / "adapter-claude-code.yaml", examples / "policy.yaml",
            )
            self.assertEqual("sdk.dir=/x\n", client.local_properties_content)
            from esc_exec.worktree import worktree_path
            # gitignored -- copying it in must not itself count as a change that
            # keeps the worktree around for review.
            self.assertFalse(worktree_path(repository, "task-index-review").is_dir())

    def test_local_workspace_kind_is_unaffected_no_worktree_created(self):
        framework = Path(__file__).parents[1]
        examples = framework / "examples/contracts"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            repository = self._repository(root)  # not a git repo at all
            add_route(registry, "repositories", "ampm-backend", repository)
            run_dir = ClaudeCodeAdapter(FakeClaudeCodeClient(), registry).execute(
                examples / "task.yaml", examples / "workspace.yaml",  # kind: local
                examples / "adapter-claude-code.yaml", examples / "policy.yaml",
            )
            run = json.loads((run_dir / "run.json").read_text())
            self.assertIsNone(run["bindings"]["worktree"])

    _repository = staticmethod(ClaudeCodeAdapterTests._repository)


class ClaudeCodeClientAskTests(unittest.TestCase):
    def test_ask_parses_json_output_without_verbose_flag(self):
        fake_result = MagicMock(returncode=0, stdout='{"result": "ok", "is_error": false}', stderr="")
        with patch("esc_exec.claude_code_adapter.subprocess.run", return_value=fake_result) as mock_run:
            outcome = ClaudeCodeClient().ask(Path("/tmp"), "prompt", ["Read"])
            self.assertEqual({"result": "ok", "is_error": False}, outcome)
            command = mock_run.call_args.args[0]
            self.assertIn("json", command)
            self.assertNotIn("--verbose", command)
            self.assertNotIn("stream-json", command)

    def test_ask_raises_on_non_json_output(self):
        fake_result = MagicMock(returncode=0, stdout="not json", stderr="")
        with patch("esc_exec.claude_code_adapter.subprocess.run", return_value=fake_result):
            with self.assertRaises(ClaudeCodeError):
                ClaudeCodeClient().ask(Path("/tmp"), "prompt", ["Read"])

    def test_ask_raises_on_nonzero_exit(self):
        fake_result = MagicMock(returncode=1, stdout="", stderr="rate limited")
        with patch("esc_exec.claude_code_adapter.subprocess.run", return_value=fake_result):
            with self.assertRaisesRegex(ClaudeCodeError, "rate limited"):
                ClaudeCodeClient().ask(Path("/tmp"), "prompt", ["Read"])


class HardDenySettingsTests(unittest.TestCase):
    """
    See plan/future/pre-flight-consent-and-bounded-autonomy.md layer 3: a small,
    static, universal deny list carried via --settings on every invocation,
    independent of whatever a task's policy otherwise grants.
    """

    def test_run_carries_hard_deny_settings(self):
        fake_result = MagicMock(returncode=0, stdout=json.dumps(_stream_json()[-1]) + "\n", stderr="")
        with patch("esc_exec.claude_code_adapter.subprocess.run", return_value=fake_result) as mock_run:
            ClaudeCodeClient().run(Path("/tmp"), "prompt", ["Bash"])
            command = mock_run.call_args.args[0]
            self.assertIn("--settings", command)
            settings = json.loads(command[command.index("--settings") + 1])
            self.assertIn("Bash(rm -rf*)", settings["permissions"]["deny"])
            self.assertIn("Bash(git push --force*)", settings["permissions"]["deny"])
            self.assertIn("Bash(git reset --hard*)", settings["permissions"]["deny"])
            self.assertIn("Read(**/.env)", settings["permissions"]["deny"])
            self.assertIn("Edit(**/.git/**)", settings["permissions"]["deny"])

    def test_ask_also_carries_hard_deny_settings(self):
        fake_result = MagicMock(returncode=0, stdout='{"result": "ok", "is_error": false}', stderr="")
        with patch("esc_exec.claude_code_adapter.subprocess.run", return_value=fake_result) as mock_run:
            ClaudeCodeClient().ask(Path("/tmp"), "prompt", ["Read"])
            command = mock_run.call_args.args[0]
            self.assertIn("--settings", command)

    def test_hard_deny_settings_is_valid_json_regardless_of_tool_grant(self):
        fake_result = MagicMock(returncode=0, stdout=json.dumps(_stream_json()[-1]) + "\n", stderr="")
        with patch("esc_exec.claude_code_adapter.subprocess.run", return_value=fake_result) as mock_run:
            ClaudeCodeClient().run(Path("/tmp"), "prompt", [])
            command = mock_run.call_args.args[0]
            settings = json.loads(command[command.index("--settings") + 1])
            self.assertTrue(settings["permissions"]["deny"])


class FakeAskClient:
    def __init__(self, outcome):
        self.outcome, self.calls = outcome, []

    def ask(self, directory, prompt, tools, model=None):
        self.calls.append((directory, prompt, tools))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class SuggestOnboardingAnswersTests(unittest.TestCase):
    def test_returns_empty_for_no_components(self):
        client = FakeAskClient({})
        self.assertEqual({}, suggest_onboarding_answers(client, Path("/tmp"), [], []))
        self.assertEqual([], client.calls)  # never even calls out for an empty request

    def test_parses_purpose_and_frameworks_together(self):
        client = FakeAskClient({
            "result": json.dumps({
                "core-api": {"purpose": "Owns the core API.", "frameworks": {"network": "ktor"}, "targets": []},
                "feature": {"purpose": "Owns the feature."},
            }),
            "is_error": False,
        })
        result = suggest_onboarding_answers(client, Path("/tmp"), ["core-api", "feature"], ["core-api"])
        self.assertEqual({
            "core-api": {"purpose": "Owns the core API.", "frameworks": {"network": "ktor"}, "targets": []},
            "feature": {"purpose": "Owns the feature."},
        }, result)

    def test_mixed_applicability_never_leaks_a_field_to_a_component_not_asked_about_it(self):
        # Regression: a component only asked about `purpose` must never get
        # `frameworks`/`targets` extracted even when ANOTHER component in the same
        # batched call was asked about frameworks -- both fields being non-empty in
        # the same call previously caused parse_groundable_response to treat every
        # requested component as applicable to every requested field.
        client = FakeAskClient({
            "result": json.dumps({
                "core-api": {"purpose": "Owns the core API.", "frameworks": {"network": "ktor"}},
                "feature": {"purpose": "Owns the feature.", "frameworks": {"network": "ktor"}},
            }),
            "is_error": False,
        })
        result = suggest_onboarding_answers(client, Path("/tmp"), ["core-api", "feature"], ["core-api"])
        self.assertEqual({
            "core-api": {"purpose": "Owns the core API.", "frameworks": {"network": "ktor"}},
            "feature": {"purpose": "Owns the feature."},
        }, result)

    def test_confidently_empty_frameworks_and_targets_are_kept(self):
        # {} and [] are valid, confident answers ("looked, found nothing") -- must
        # not be dropped as if they were missing/unanswered.
        client = FakeAskClient({
            "result": json.dumps({"core-api": {"frameworks": {}, "targets": []}}),
            "is_error": False,
        })
        result = suggest_onboarding_answers(client, Path("/tmp"), [], ["core-api"])
        self.assertEqual({"core-api": {"frameworks": {}, "targets": []}}, result)

    def test_purpose_only_requested_ignores_frameworks_in_response(self):
        # A component that was only asked about `purpose` shouldn't have frameworks/
        # targets smuggled in even if the model included them anyway.
        client = FakeAskClient({
            "result": json.dumps({"core-api": {"purpose": "Owns the core API.", "frameworks": {"network": "ktor"}}}),
            "is_error": False,
        })
        result = suggest_onboarding_answers(client, Path("/tmp"), ["core-api"], [])
        self.assertEqual({"core-api": {"purpose": "Owns the core API."}}, result)

    def test_strips_markdown_fences(self):
        client = FakeAskClient({"result": '```json\n{"core-api": {"purpose": "Owns the core API."}}\n```', "is_error": False})
        result = suggest_onboarding_answers(client, Path("/tmp"), ["core-api"], [])
        self.assertEqual({"core-api": {"purpose": "Owns the core API."}}, result)

    def test_extracts_json_when_model_adds_commentary_before_the_fence(self):
        # Regression test for a real bug caught live against arrow-errors: despite
        # an explicit "no commentary" instruction, the model prefaced its answer with
        # a full sentence before the fenced JSON block. The previous parser only
        # handled a fence at the very start of the text and silently dropped every
        # suggestion (not just the field the commentary was about) when that happened.
        client = FakeAskClient({
            "result": (
                "This is an Android-only demo app (no KMP target block), so no "
                "`targets` to report.\n\n```json\n"
                '{"sample": {"purpose": "Demo Android app.", "frameworks": {"ui": "jetpack-compose"}, "targets": []}}'
                "\n```"
            ),
            "is_error": False,
        })
        result = suggest_onboarding_answers(client, Path("/tmp"), ["sample"], ["sample"])
        self.assertEqual(
            {"sample": {"purpose": "Demo Android app.", "frameworks": {"ui": "jetpack-compose"}, "targets": []}},
            result,
        )

    def test_extracts_json_with_no_fence_and_no_commentary(self):
        client = FakeAskClient({"result": '{"core-api": {"purpose": "Owns the core API."}}', "is_error": False})
        result = suggest_onboarding_answers(client, Path("/tmp"), ["core-api"], [])
        self.assertEqual({"core-api": {"purpose": "Owns the core API."}}, result)

    def test_is_error_fails_open(self):
        client = FakeAskClient({"result": "boom", "is_error": True})
        self.assertEqual({}, suggest_onboarding_answers(client, Path("/tmp"), ["core-api"], []))

    def test_non_json_result_fails_open(self):
        client = FakeAskClient({"result": "not json at all", "is_error": False})
        self.assertEqual({}, suggest_onboarding_answers(client, Path("/tmp"), ["core-api"], []))

    def test_client_error_fails_open(self):
        client = FakeAskClient(ClaudeCodeError("boom"))
        self.assertEqual({}, suggest_onboarding_answers(client, Path("/tmp"), ["core-api"], []))

    def test_malformed_frameworks_shape_is_dropped_not_invented(self):
        client = FakeAskClient({
            "result": json.dumps({"core-api": {"purpose": "Owns the core API.", "frameworks": ["not", "a", "dict"]}}),
            "is_error": False,
        })
        result = suggest_onboarding_answers(client, Path("/tmp"), ["core-api"], ["core-api"])
        self.assertEqual({"core-api": {"purpose": "Owns the core API."}}, result)

    def test_unknown_component_id_in_response_is_dropped(self):
        client = FakeAskClient({
            "result": json.dumps({
                "core-api": {"purpose": "Owns the core API."}, "unknown-id": {"purpose": "Should be dropped."},
            }),
            "is_error": False,
        })
        result = suggest_onboarding_answers(client, Path("/tmp"), ["core-api"], [])
        self.assertEqual({"core-api": {"purpose": "Owns the core API."}}, result)

    def test_single_batched_call_covers_every_component_and_field(self):
        client = FakeAskClient({"result": "{}", "is_error": False})
        suggest_onboarding_answers(client, Path("/tmp"), ["core-api", "feature"], ["feature"])
        self.assertEqual(1, len(client.calls))
        prompt = client.calls[0][1]
        self.assertIn("core-api, feature", prompt)
        self.assertIn("feature", prompt)

    def test_only_read_only_tools_are_granted(self):
        client = FakeAskClient({"result": "{}", "is_error": False})
        suggest_onboarding_answers(client, Path("/tmp"), ["core-api"], [])
        self.assertEqual(["Read", "Glob", "Grep"], client.calls[0][2])


class ExtractArchitectureStyleTests(unittest.TestCase):
    def test_extracts_web_app(self):
        self.assertEqual({"architecture_style": "web-app"}, _extract_architecture_style({"architecture_style": "web-app"}))

    def test_extracts_web_content(self):
        self.assertEqual(
            {"architecture_style": "web-content"}, _extract_architecture_style({"architecture_style": "web-content"}),
        )

    def test_missing_key_is_absent(self):
        self.assertEqual({}, _extract_architecture_style({}))

    def test_invalid_string_value_is_dropped_not_coerced(self):
        self.assertEqual({}, _extract_architecture_style({"architecture_style": "web-something-else"}))

    def test_wrong_type_is_dropped(self):
        self.assertEqual({}, _extract_architecture_style({"architecture_style": ["web-app"]}))
        self.assertEqual({}, _extract_architecture_style({"architecture_style": None}))
        self.assertEqual({}, _extract_architecture_style({"architecture_style": 1}))


class ArchitectureStyleGroundableFieldFlowTests(unittest.TestCase):
    # Confirms the new `architecture_style` GROUNDABLE_FIELDS entry flows through
    # the shared one-shot engine (`_suggest_groundable_answers`, the function
    # `suggest_onboarding_answers` itself delegates to) automatically -- adding a
    # registry entry, not a new bespoke code path. The turn-based twin
    # (`suggest_groundable_answers_turn` in esc_exec/conversation.py) is verified
    # separately in tests/test_conversation.py since it shares this exact
    # prompt-building/parsing machinery.
    def test_architecture_style_is_requested_and_extracted(self):
        client = FakeAskClient({
            "result": json.dumps({"web-ui": {"architecture_style": "web-app"}}),
            "is_error": False,
        })
        result = _suggest_groundable_answers(client, Path("/tmp"), {"architecture_style": {"web-ui"}})
        self.assertEqual({"web-ui": {"architecture_style": "web-app"}}, result)
        self.assertIn("architecture_style", client.calls[0][1])
        self.assertIn("web-ui", client.calls[0][1])

    def test_unanswered_architecture_style_is_a_valid_absence_not_an_error(self):
        client = FakeAskClient({"result": json.dumps({"web-ui": {}}), "is_error": False})
        result = _suggest_groundable_answers(client, Path("/tmp"), {"architecture_style": {"web-ui"}})
        self.assertEqual({}, result)

    def test_invented_style_value_is_never_smuggled_through(self):
        client = FakeAskClient({
            "result": json.dumps({"web-ui": {"architecture_style": "microservice"}}),
            "is_error": False,
        })
        result = _suggest_groundable_answers(client, Path("/tmp"), {"architecture_style": {"web-ui"}})
        self.assertEqual({}, result)

    def test_architecture_style_never_leaks_to_a_component_not_asked_about_it(self):
        client = FakeAskClient({
            "result": json.dumps({
                "web-ui": {"purpose": "Owns the web UI.", "architecture_style": "web-content"},
                "core-api": {"purpose": "Owns the core API.", "architecture_style": "web-app"},
            }),
            "is_error": False,
        })
        result = _suggest_groundable_answers(
            client, Path("/tmp"), {"purpose": {"web-ui", "core-api"}, "architecture_style": {"web-ui"}},
        )
        self.assertEqual({
            "web-ui": {"purpose": "Owns the web UI.", "architecture_style": "web-content"},
            "core-api": {"purpose": "Owns the core API."},
        }, result)


class SuggestWorkTypeDriftTests(unittest.TestCase):
    def _call(self, client, work_type="fix", objective="Fix the broken login page.",
               scope_boundary="No new auth providers.", completion_conditions=("Login works again.",)):
        return suggest_work_type_drift(
            client, Path("/tmp"), work_type, objective, scope_boundary, list(completion_conditions),
        )

    def test_no_drift_reported_is_returned_as_is(self):
        client = FakeAskClient({"result": json.dumps({"drifted": False}), "is_error": False})
        result = self._call(client)
        self.assertEqual({"drifted": False, "suggested_work_type": None, "reasoning": None}, result)

    def test_drift_with_valid_type_and_reasoning_is_returned(self):
        payload = {
            "drifted": True, "suggested_work_type": "feature",
            "reasoning": "This adds a new password-reset flow, not just a correction.",
        }
        client = FakeAskClient({"result": json.dumps(payload), "is_error": False})
        result = self._call(client)
        self.assertEqual(payload, result)

    def test_drift_without_reasoning_is_dropped_not_invented(self):
        client = FakeAskClient({
            "result": json.dumps({"drifted": True, "suggested_work_type": "feature", "reasoning": None}),
            "is_error": False,
        })
        result = self._call(client)
        self.assertFalse(result["drifted"])

    def test_drift_with_reasoning_but_empty_string_is_dropped(self):
        client = FakeAskClient({
            "result": json.dumps({"drifted": True, "suggested_work_type": "feature", "reasoning": "   "}),
            "is_error": False,
        })
        result = self._call(client)
        self.assertFalse(result["drifted"])

    def test_suggested_type_outside_known_work_types_is_dropped(self):
        client = FakeAskClient({
            "result": json.dumps({"drifted": True, "suggested_work_type": "rewrite-everything", "reasoning": "..."}),
            "is_error": False,
        })
        result = self._call(client)
        self.assertFalse(result["drifted"])

    def test_suggested_type_same_as_declared_is_dropped(self):
        client = FakeAskClient({
            "result": json.dumps({"drifted": True, "suggested_work_type": "fix", "reasoning": "..."}),
            "is_error": False,
        })
        result = self._call(client, work_type="fix")
        self.assertFalse(result["drifted"])

    def test_client_error_fails_open(self):
        client = FakeAskClient(ClaudeCodeError("boom"))
        result = self._call(client)
        self.assertEqual({"drifted": False, "suggested_work_type": None, "reasoning": None}, result)

    def test_is_error_fails_open(self):
        client = FakeAskClient({"result": "irrelevant", "is_error": True})
        result = self._call(client)
        self.assertFalse(result["drifted"])

    def test_non_json_result_fails_open(self):
        client = FakeAskClient({"result": "not json at all", "is_error": False})
        result = self._call(client)
        self.assertFalse(result["drifted"])

    def test_no_tools_are_granted(self):
        client = FakeAskClient({"result": json.dumps({"drifted": False}), "is_error": False})
        self._call(client)
        self.assertEqual([], client.calls[0][2])

    def test_prompt_includes_declared_fields_and_valid_work_types(self):
        client = FakeAskClient({"result": json.dumps({"drifted": False}), "is_error": False})
        self._call(client, work_type="fix", objective="Fix the broken login page.")
        prompt = client.calls[0][1]
        self.assertIn("Declared work_type: fix", prompt)
        self.assertIn("Fix the broken login page.", prompt)
        self.assertIn("feature, fix, refactor, maintenance, investigation", prompt)

    def test_empty_completion_conditions_does_not_crash(self):
        client = FakeAskClient({"result": json.dumps({"drifted": False}), "is_error": False})
        result = self._call(client, completion_conditions=())
        self.assertFalse(result["drifted"])
        self.assertIn("(none stated)", client.calls[0][1])


class SuggestArchitectureCoverageGapTests(unittest.TestCase):
    RESOLVED_DOCUMENTS = [
        {"id": "PAT-DATA-ACCESS", "path": "patterns/data-access-abstraction.md", "tags": ["data-access", "repository"]},
    ]

    def test_no_resolved_documents_is_a_gap_without_calling_out(self):
        client = FakeAskClient({"result": "irrelevant", "is_error": False})
        result = suggest_architecture_coverage_gap(client, Path("/tmp"), "Add background jobs.", [])
        self.assertFalse(result["covered"])
        self.assertIsNone(result["suggested_title"])
        self.assertEqual([], client.calls)

    def test_covered_is_returned_as_is(self):
        client = FakeAskClient({"result": json.dumps({"covered": True}), "is_error": False})
        result = suggest_architecture_coverage_gap(client, Path("/tmp"), "Add a REST endpoint.", self.RESOLVED_DOCUMENTS)
        self.assertEqual({"covered": True, "reasoning": None, "suggested_title": None}, result)

    def test_gap_with_reasoning_and_title_is_returned(self):
        payload = {
            "covered": False,
            "reasoning": "None of these documents cover background job scheduling.",
            "suggested_title": "Background Job Scheduling",
        }
        client = FakeAskClient({"result": json.dumps(payload), "is_error": False})
        result = suggest_architecture_coverage_gap(client, Path("/tmp"), "Add background jobs.", self.RESOLVED_DOCUMENTS)
        self.assertEqual(payload, result)

    def test_gap_without_reasoning_is_dropped_not_invented(self):
        client = FakeAskClient({
            "result": json.dumps({"covered": False, "reasoning": None, "suggested_title": "X"}),
            "is_error": False,
        })
        result = suggest_architecture_coverage_gap(client, Path("/tmp"), "Add background jobs.", self.RESOLVED_DOCUMENTS)
        self.assertTrue(result["covered"])

    def test_gap_without_suggested_title_is_dropped_not_invented(self):
        client = FakeAskClient({
            "result": json.dumps({"covered": False, "reasoning": "Not covered.", "suggested_title": None}),
            "is_error": False,
        })
        result = suggest_architecture_coverage_gap(client, Path("/tmp"), "Add background jobs.", self.RESOLVED_DOCUMENTS)
        self.assertTrue(result["covered"])

    def test_client_error_fails_open_to_covered(self):
        client = FakeAskClient(ClaudeCodeError("boom"))
        result = suggest_architecture_coverage_gap(client, Path("/tmp"), "Add background jobs.", self.RESOLVED_DOCUMENTS)
        self.assertTrue(result["covered"])

    def test_is_error_fails_open_to_covered(self):
        client = FakeAskClient({"result": "irrelevant", "is_error": True})
        result = suggest_architecture_coverage_gap(client, Path("/tmp"), "Add background jobs.", self.RESOLVED_DOCUMENTS)
        self.assertTrue(result["covered"])

    def test_non_json_result_fails_open_to_covered(self):
        client = FakeAskClient({"result": "not json at all", "is_error": False})
        result = suggest_architecture_coverage_gap(client, Path("/tmp"), "Add background jobs.", self.RESOLVED_DOCUMENTS)
        self.assertTrue(result["covered"])

    def test_read_glob_grep_are_granted(self):
        client = FakeAskClient({"result": json.dumps({"covered": True}), "is_error": False})
        suggest_architecture_coverage_gap(client, Path("/tmp"), "Add a REST endpoint.", self.RESOLVED_DOCUMENTS)
        self.assertEqual(["Read", "Glob", "Grep"], client.calls[0][2])

    def test_prompt_includes_document_ids_and_objective(self):
        client = FakeAskClient({"result": json.dumps({"covered": True}), "is_error": False})
        suggest_architecture_coverage_gap(client, Path("/tmp"), "Add background jobs.", self.RESOLVED_DOCUMENTS)
        prompt = client.calls[0][1]
        self.assertIn("PAT-DATA-ACCESS", prompt)
        self.assertIn("patterns/data-access-abstraction.md", prompt)
        self.assertIn("Add background jobs.", prompt)


class ClaudeAuthStatusTests(unittest.TestCase):
    def test_parses_logged_in_json(self):
        fake_result = MagicMock(returncode=0, stdout=json.dumps({
            "loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "pro",
        }))
        with patch("esc_exec.claude_code_adapter.subprocess.run", return_value=fake_result):
            status = claude_auth_status()
            self.assertEqual(True, status["loggedIn"])
            self.assertEqual("pro", status["subscriptionType"])

    def test_nonzero_exit_returns_none(self):
        fake_result = MagicMock(returncode=1, stdout="")
        with patch("esc_exec.claude_code_adapter.subprocess.run", return_value=fake_result):
            self.assertIsNone(claude_auth_status())

    def test_missing_binary_returns_none(self):
        with patch("esc_exec.claude_code_adapter.subprocess.run", side_effect=FileNotFoundError):
            self.assertIsNone(claude_auth_status())

    def test_unparseable_output_returns_none(self):
        fake_result = MagicMock(returncode=0, stdout="not json")
        with patch("esc_exec.claude_code_adapter.subprocess.run", return_value=fake_result):
            self.assertIsNone(claude_auth_status())


if __name__ == "__main__":
    unittest.main()
