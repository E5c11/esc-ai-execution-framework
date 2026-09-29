"""The `root_cause` stage of the `fix` procedure: validation, the planning gate, the persisted task, the
contract check, the agent prompt, and the procedure's own claim that it is enforced."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.claude_code_adapter import ClaudeCodeAdapter
from esc_exec.codex_adapter import CodexAdapter
from esc_exec.contracts import validate_contract
from esc_exec.model import ManifestState
from esc_exec.opencode_adapter import OpenCodeAdapter
from esc_exec.planning import (
    generate_multi_repository_workflow,
    generate_single_repository_workflow,
    planning_questions,
)
from esc_exec.procedures import PROCEDURES, ROOT_CAUSE
from esc_exec.registry import add_route
from esc_exec.root_cause import root_cause_prompt_lines, validate_root_cause
from esc_exec.yaml_io import load_yaml, write_yaml
from tests.test_planning import PlanningTests as _PlanningTests

GOOD = {"statement": "Export skips the last row because the loop bound is len-1.", "evidence": ["export.py:42", "repro fails"]}
OBJECTIVE = "CSV export drops the last row"


class ValidateRootCauseTests(unittest.TestCase):
    def test_accepts_a_statement_with_evidence_and_canonicalises_it(self):
        result = validate_root_cause({"statement": "  Off by one.  ", "evidence": [" a ", "", "b"], "reproduction": " run x "}, OBJECTIVE)
        self.assertEqual({"statement": "Off by one.", "evidence": ["a", "b"], "reproduction": "run x"}, result)

    def test_evidence_may_be_one_string_of_semicolon_separated_items(self):
        self.assertEqual(["a", "b"], validate_root_cause({"statement": "s", "evidence": "a; b"})["evidence"])

    def test_rejects_the_ways_a_root_cause_is_captured_in_name_only(self):
        cases = {
            "not an object": "the loop is wrong",
            "no statement": {"evidence": ["x"]},
            "blank statement": {"statement": "  ", "evidence": ["x"]},
            "no evidence": {"statement": "s"},
            "empty evidence": {"statement": "s", "evidence": ["", "  "]},
            "unknown field": {"statement": "s", "evidence": ["x"], "confidence": "high"},
            "blank reproduction": {"statement": "s", "evidence": ["x"], "reproduction": " "},
        }
        for name, value in cases.items():
            with self.subTest(case=name), self.assertRaises(ValueError):
                validate_root_cause(value, OBJECTIVE)

    def test_a_statement_that_only_restates_the_objective_is_a_symptom_not_a_cause(self):
        with self.assertRaisesRegex(ValueError, "restates the objective"):
            validate_root_cause({"statement": "csv EXPORT drops the last row!", "evidence": ["x"]}, OBJECTIVE)


class QuestionTests(unittest.TestCase):
    def test_a_fix_asks_for_the_root_cause_first_and_other_work_types_do_not(self):
        fix = [q["field"] for q in planning_questions({"repo": []}, "fix")]
        self.assertEqual(["root_cause_statement", "root_cause_evidence", "root_cause_reproduction"], fix[:3])
        for work_type in (None, "feature", "refactor", "maintenance", "investigation"):
            with self.subTest(work_type=work_type):
                self.assertFalse([q for q in planning_questions({"repo": []}, work_type) if q["field"].startswith("root_cause")])


class _RepositoryCase(unittest.TestCase):
    """A one-repository fixture (borrows the builder only, so test_planning's own tests are not re-run)."""

    _make_repository = _PlanningTests._make_repository

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self._make_repository(self.root, "repo", "content", "Owns curriculum and lesson export.", ["export"])

    def tearDown(self):
        self.temp.cleanup()


class PlanningGateTests(_RepositoryCase):
    def _single(self, work_type="fix", root_cause=GOOD, task_id="fix-export"):
        return generate_single_repository_workflow(
            self.root, "repo", task_id, OBJECTIVE, work_type, ["content"], "", ["rows are complete"], root_cause=root_cause,
        )

    def test_a_fix_cannot_be_planned_without_a_root_cause_and_nothing_is_written(self):
        with self.assertRaisesRegex(ValueError, "root cause"):
            self._single(root_cause=None)
        self.assertFalse((self.root / ".esc-ai" / "workflows" / "active" / "fix-export").exists())

    def test_a_malformed_root_cause_is_rejected_before_anything_is_written(self):
        with self.assertRaises(ValueError):
            self._single(root_cause={"statement": OBJECTIVE, "evidence": ["x"]})
        self.assertFalse((self.root / ".esc-ai" / "workflows" / "active" / "fix-export").exists())

    def test_a_fix_task_records_its_work_type_and_root_cause_and_validates(self):
        task_path, readme_path = self._single()
        document = load_yaml(task_path)
        self.assertEqual("fix", document["task"]["work_type"])
        self.assertEqual(GOOD, document["root_cause"])
        self.assertEqual(ManifestState.VALID, validate_contract("task", task_path).state)
        readme = readme_path.read_text()
        self.assertIn("## Root cause", readme)
        self.assertIn("loop bound is len-1", readme)
        self.assertLess(readme.index("## Root cause"), readme.index("## Components"))

    def test_other_work_types_record_their_type_and_need_no_root_cause(self):
        task_path, _ = self._single(work_type="feature", root_cause=None, task_id="feature-x")
        document = load_yaml(task_path)
        self.assertEqual("feature", document["task"]["work_type"])
        self.assertNotIn("root_cause", document)
        self.assertEqual(ManifestState.VALID, validate_contract("task", task_path).state)

    def test_a_task_predating_work_type_stays_valid(self):
        task_path, _ = self._single(work_type="feature", root_cause=None, task_id="legacy")
        document = load_yaml(task_path)
        del document["task"]["work_type"]
        write_yaml(task_path, document)
        self.assertEqual(ManifestState.VALID, validate_contract("task", task_path).state)

    def test_a_hand_edited_fix_task_without_a_root_cause_is_not_executable(self):
        task_path, _ = self._single()
        document = load_yaml(task_path)
        del document["root_cause"]
        write_yaml(task_path, document)
        result = validate_contract("task", task_path)
        self.assertEqual(ManifestState.INVALID, result.state)
        self.assertTrue(any("root_cause is required for a fix task" in message for message in result.messages))

    def test_a_hand_edited_symptom_only_root_cause_is_not_executable(self):
        task_path, _ = self._single()
        document = load_yaml(task_path)
        document["root_cause"] = {"statement": OBJECTIVE, "evidence": ["x"]}
        write_yaml(task_path, document)
        self.assertEqual(ManifestState.INVALID, validate_contract("task", task_path).state)

    def test_an_unknown_work_type_is_invalid(self):
        task_path, _ = self._single()
        document = load_yaml(task_path)
        document["task"]["work_type"] = "sorcery"
        write_yaml(task_path, document)
        self.assertEqual(ManifestState.INVALID, validate_contract("task", task_path).state)


class MultiRepositoryTests(_RepositoryCase):
    def setUp(self):
        super().setUp()
        self.other = Path(self.temp.name) / "other"
        self._make_repository(self.other, "other", "api", "Serves the export API.", ["export"])
        self.registry = Path(self.temp.name) / "registry.yaml"
        add_route(self.registry, "repositories", "repo", self.root)
        add_route(self.registry, "repositories", "other", self.other)
        self.tasks = {
            "repo": {"task_id": "fix-repo", "components": ["content"], "completion_conditions": ["ok"]},
            "other": {"task_id": "fix-other", "components": ["api"], "completion_conditions": ["ok"], "depends_on": ["repo/fix-repo"]},
        }

    def test_one_root_cause_is_recorded_in_every_repositorys_task(self):
        written = generate_multi_repository_workflow(self.registry, "fix-x", OBJECTIVE, "fix", self.tasks, root_cause=GOOD)
        for repository_id, (task_path, _) in written.items():
            with self.subTest(repository=repository_id):
                self.assertEqual(GOOD, load_yaml(task_path)["root_cause"])
                self.assertEqual("fix", load_yaml(task_path)["task"]["work_type"])

    def test_a_missing_or_bad_root_cause_touches_no_repository(self):
        for bad in (None, {"statement": "", "evidence": []}):
            with self.subTest(root_cause=bad), self.assertRaises(ValueError):
                generate_multi_repository_workflow(self.registry, "fix-x", OBJECTIVE, "fix", self.tasks, root_cause=bad)
        for repository in (self.root, self.other):
            self.assertFalse((repository / ".esc-ai" / "workflows" / "active").exists())


class AgentSeesTheRootCauseTests(unittest.TestCase):
    CONTEXT = {
        "task": {"id": "t", "repository": "r", "objective": OBJECTIVE, "work_type": "fix", "root_cause": {**GOOD, "reproduction": "pytest -k export"}},
        "routing": {"repository_index": ".esc-ai/esc-index.json", "components": []},
        "scope": {"paths": []},
    }

    def test_the_shared_lines_state_cause_evidence_reproduction_and_the_instruction(self):
        text = "\n".join(root_cause_prompt_lines(self.CONTEXT))
        for expected in ("loop bound is len-1", "Evidence: export.py:42", "Reproduce with: pytest -k export", "not just the symptom"):
            self.assertIn(expected, text)

    def test_a_task_without_a_root_cause_adds_nothing(self):
        self.assertEqual([], root_cause_prompt_lines({"task": {"objective": "x"}}))

    def test_every_adapter_puts_it_in_the_real_prompt(self):
        with TemporaryDirectory() as temp:
            repository = Path(temp)
            prompts = {
                "claude": ClaudeCodeAdapter(None, repository / "registry.yaml")._prompt(self.CONTEXT, ["Read"], repository),
                "codex": CodexAdapter(None, repository / "registry.yaml")._prompt(self.CONTEXT, "read-only", repository),
                "opencode": OpenCodeAdapter._prompt(self.CONTEXT, {"read": True, "edit": False, "bash": False, "webfetch": False}, repository),
            }
        for name, prompt in prompts.items():
            with self.subTest(adapter=name):
                self.assertIn("loop bound is len-1", prompt)
                self.assertLess(prompt.index("Root cause"), prompt.index("Declared components"))


class ProcedureClaimTests(unittest.TestCase):
    def test_the_fix_procedure_includes_root_cause_before_the_plan_is_produced(self):
        names = [stage.name for stage in PROCEDURES["fix"]]
        self.assertLess(names.index("root_cause"), names.index("plan_produce"))
        self.assertLess(names.index("root_cause"), names.index("implement"))

    def test_root_cause_is_no_longer_declared_new_and_names_its_enforcement(self):
        self.assertFalse(ROOT_CAUSE.maps_to.startswith("new"))
        self.assertIn("esc_exec.root_cause.validate_root_cause", ROOT_CAUSE.maps_to)
        self.assertEqual("gate", ROOT_CAUSE.kind)
        self.assertEqual("fixed", ROOT_CAUSE.interaction)
        self.assertTrue(ROOT_CAUSE.mandatory)


if __name__ == "__main__":
    unittest.main()
