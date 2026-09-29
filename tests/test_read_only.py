"""Read-only work (`plan`, `investigation`): the forced policy, the repository snapshot and the violation check,
and the per-work-type prompt lines."""
import copy
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from esc_exec.claude_code_adapter import ClaudeCodeAdapter
from esc_exec.codex_adapter import CodexAdapter
from esc_exec.contracts import validate_contract
from esc_exec.model import ManifestState
from esc_exec.opencode_adapter import OpenCodeAdapter
from esc_exec.planning import WORK_TYPES
from esc_exec.procedure_prompt import WORK_TYPE_LINES, procedure_prompt_lines
from esc_exec.read_only import READ_ONLY_WORK_TYPES, effective_policy, is_read_only, state_violations
from esc_exec.worktree import repository_state
from esc_exec.yaml_io import write_yaml

POLICY = {
    "schema_version": 1,
    "policy": {"id": "standard-autonomous", "description": "d"},
    "permissions": {"read": "allow", "edit": "allow", "execute": "allow", "network": "allow", "external_paths": "deny"},
    "approvals": ["execute"],
}


def git(repository: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repository), *args], capture_output=True, text=True, check=True)


def make_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    (root / "a.txt").write_text("one\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


class WorkTypeTests(unittest.TestCase):
    def test_plan_and_document_are_engine_work_types_and_validate(self):
        self.assertIn("plan", WORK_TYPES)
        self.assertIn("document", WORK_TYPES)
        for work_type in WORK_TYPES:
            with self.subTest(work_type=work_type), TemporaryDirectory() as temp:
                path = Path(temp) / "task.yaml"
                write_yaml(path, {
                    "schema_version": 1,
                    "task": {"id": "t", "title": "t", "objective": "o", "repository": "r", "status": "ready", "work_type": work_type},
                    "scope": {"components": ["c"]}, "completion_conditions": ["done"],
                    **({"root_cause": {"statement": "x", "evidence": ["y"]}} if work_type == "fix" else {}),
                })
                self.assertEqual(ManifestState.VALID, validate_contract("task", path).state)

    def test_only_plan_and_investigation_are_read_only(self):
        self.assertEqual({"plan", "investigation"}, set(READ_ONLY_WORK_TYPES))
        for work_type in ("feature", "fix", "refactor", "maintenance", "document", None):
            self.assertFalse(is_read_only(work_type))


class EffectivePolicyTests(unittest.TestCase):
    def test_read_only_work_is_denied_everything_that_can_change_state(self):
        for work_type in READ_ONLY_WORK_TYPES:
            with self.subTest(work_type=work_type):
                permissions = effective_policy(POLICY, work_type)["permissions"]
                self.assertEqual("allow", permissions["read"])
                for category in ("edit", "execute", "network", "external_paths"):
                    self.assertEqual("deny", permissions[category], category)

    def test_approvals_are_dropped_because_nothing_is_left_to_approve(self):
        self.assertNotIn("approvals", effective_policy(POLICY, "plan"))

    def test_the_input_is_not_mutated_and_other_work_gets_the_same_document(self):
        before = copy.deepcopy(POLICY)
        effective_policy(POLICY, "investigation")
        self.assertEqual(before, POLICY)
        for work_type in ("feature", "fix", "refactor", "document"):
            self.assertIs(POLICY, effective_policy(POLICY, work_type))

    def test_it_is_never_more_permissive_than_the_configured_policy(self):
        stricter = {**POLICY, "permissions": {**POLICY["permissions"], "read": "deny"}}
        self.assertEqual("deny", effective_policy(stricter, "plan")["permissions"]["read"])

    def test_a_policy_without_permissions_still_yields_a_read_only_grant(self):
        permissions = effective_policy({"schema_version": 1, "policy": {"id": "p", "description": "d"}}, "plan")["permissions"]
        self.assertEqual({"read": "allow", "edit": "deny", "execute": "deny", "network": "deny", "external_paths": "deny"}, permissions)


class StateViolationTests(unittest.TestCase):
    def snapshot(self, head="a" * 40, **files):
        return {"head": head, "files": files}

    def test_identical_snapshots_have_no_violations(self):
        self.assertEqual([], state_violations(self.snapshot(x="M:1"), self.snapshot(x="M:1")))

    def test_each_kind_of_change_is_named(self):
        cases = {
            "new file": (self.snapshot(), self.snapshot(new="??:1"), "new: created or newly modified"),
            "modified again": (self.snapshot(f="M:1"), self.snapshot(f="M:2"), "f: modified again"),
            "restored": (self.snapshot(f="M:1"), self.snapshot(), "f: a previously changed file was restored or removed"),
            "head moved": (self.snapshot(head="a" * 40), self.snapshot(head="b" * 40), "HEAD moved"),
        }
        for name, (before, after, expected) in cases.items():
            with self.subTest(case=name):
                violations = state_violations(before, after)
                self.assertTrue(any(expected in violation for violation in violations), violations)

    def test_escape_ai_bookkeeping_directories_are_ignored(self):
        after = self.snapshot(**{".esc-ai/runs/run-1/summary.json": "??:1", ".esc-ai/worktrees/t/x": "??:2"})
        self.assertEqual([], state_violations(self.snapshot(), after))

    def test_other_esc_ai_files_are_not_ignored(self):
        after = self.snapshot(**{".esc-ai/workflows/active/t/task.yaml": "M:1"})
        self.assertEqual(1, len(state_violations(self.snapshot(), after)))

    def test_a_snapshot_that_could_not_be_taken_yields_none_rather_than_a_pass_or_a_failure(self):
        self.assertEqual([], state_violations(None, self.snapshot()))
        self.assertEqual([], state_violations(self.snapshot(), None))


class RepositoryStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.repo = make_repo(Path(self.temp.name) / "repo")

    def tearDown(self):
        self.temp.cleanup()

    def violations_after(self, mutate):
        before = repository_state(self.repo)
        mutate()
        return state_violations(before, repository_state(self.repo))

    def test_a_clean_untouched_repository_has_no_violations(self):
        self.assertEqual([], self.violations_after(lambda: None))

    def test_editing_a_tracked_file_is_detected(self):
        violations = self.violations_after(lambda: (self.repo / "a.txt").write_text("two\n"))
        self.assertEqual(["a.txt: created or newly modified"], violations)

    def test_creating_an_untracked_file_is_detected(self):
        self.assertEqual(["new.txt: created or newly modified"], self.violations_after(lambda: (self.repo / "new.txt").write_text("x")))

    def test_modifying_an_already_modified_file_again_is_detected_by_content_not_status(self):
        (self.repo / "a.txt").write_text("dirty before the run\n")
        violations = self.violations_after(lambda: (self.repo / "a.txt").write_text("changed during the run\n"))
        self.assertEqual(["a.txt: modified again"], violations)

    def test_work_already_uncommitted_before_the_run_is_not_blamed_on_it(self):
        (self.repo / "a.txt").write_text("dirty before the run\n")
        (self.repo / "wip.txt").write_text("wip\n")
        self.assertEqual([], self.violations_after(lambda: None))

    def test_a_commit_is_detected_even_though_the_tree_ends_clean(self):
        def commit():
            (self.repo / "a.txt").write_text("two\n")
            git(self.repo, "commit", "-qam", "sneaky")
        violations = self.violations_after(commit)
        self.assertTrue(any("HEAD moved" in violation for violation in violations), violations)

    def test_deleting_a_file_is_detected(self):
        self.assertEqual(1, len(self.violations_after(lambda: (self.repo / "a.txt").unlink())))

    def test_escape_ai_run_output_does_not_count(self):
        def write_run_output():
            (self.repo / ".esc-ai" / "runs" / "run-1").mkdir(parents=True)
            (self.repo / ".esc-ai" / "runs" / "run-1" / "summary.json").write_text("{}")
        self.assertEqual([], self.violations_after(write_run_output))

    def test_a_directory_that_is_not_a_git_checkout_has_no_snapshot(self):
        with TemporaryDirectory() as temp:
            self.assertIsNone(repository_state(Path(temp)))


class PromptTests(unittest.TestCase):
    def context(self, work_type):
        return {
            "task": {"id": "t", "repository": "r", "objective": "o", "work_type": work_type},
            "routing": {"repository_index": ".esc-ai/esc-index.json", "components": []},
            "scope": {"paths": []},
        }

    def test_each_special_work_type_gets_its_guidance(self):
        for work_type in ("plan", "investigation", "document", "refactor"):
            with self.subTest(work_type=work_type):
                self.assertEqual([WORK_TYPE_LINES[work_type]], procedure_prompt_lines(self.context(work_type)))

    def test_read_only_guidance_tells_the_agent_not_to_modify_anything(self):
        for work_type in READ_ONLY_WORK_TYPES:
            self.assertIn("Do not modify any file", WORK_TYPE_LINES[work_type])

    def test_work_types_without_guidance_add_nothing(self):
        for work_type in ("feature", "maintenance", None):
            self.assertEqual([], procedure_prompt_lines(self.context(work_type)))

    def test_a_fix_gets_its_root_cause_lines(self):
        context = self.context("fix")
        context["task"]["root_cause"] = {"statement": "the loop bound is wrong", "evidence": ["a.py:1"]}
        self.assertIn("the loop bound is wrong", "\n".join(procedure_prompt_lines(context)))

    def test_every_adapter_puts_the_guidance_in_the_real_prompt(self):
        with TemporaryDirectory() as temp:
            repository = Path(temp)
            context = self.context("investigation")
            prompts = {
                "claude": ClaudeCodeAdapter(None, repository / "r.yaml")._prompt(context, ["Read"], repository),
                "codex": CodexAdapter(None, repository / "r.yaml")._prompt(context, "read-only", repository),
                "opencode": OpenCodeAdapter._prompt(context, {"read": True, "edit": False, "bash": False, "webfetch": False}, repository),
            }
        for name, prompt in prompts.items():
            with self.subTest(adapter=name):
                self.assertIn("This is an investigation", prompt)
                self.assertLess(prompt.index("This is an investigation"), prompt.index("Declared components"))


if __name__ == "__main__":
    unittest.main()
