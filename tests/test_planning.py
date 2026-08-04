from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.contracts import validate_contract
from esc_exec.dependencies import generate_dependency_graph
from esc_exec.indexing import generate_indexes
from esc_exec.manifests import (
    component_manifest_path, component_manifest_relative_path, repository_manifest_path,
)
from esc_exec.model import ManifestState
from esc_exec.planning import (
    generate_multi_repository_workflow,
    generate_single_repository_workflow,
    planning_questions,
    route_objective,
)
from esc_exec.registry import add_route
from esc_exec.yaml_io import load_yaml, write_yaml


class PlanningTests(unittest.TestCase):
    def _make_repository(self, root: Path, repository_id: str, component_id: str, purpose: str, domains: list[str]) -> None:
        (root / component_id).mkdir(parents=True)
        write_yaml(repository_manifest_path(root), {
            "schema_version": 1,
            "repository": {"id": repository_id, "type": "gradle-multi-project", "purpose": "test"},
            "components": [{"id": component_id, "manifest": component_manifest_relative_path(component_id)}],
        })
        write_yaml(component_manifest_path(root, component_id), {
            "schema_version": 1,
            "component": {
                "id": component_id, "type": "gradle-module", "path": component_id, "purpose": purpose,
                "ownership": {"domains": domains},
            },
            "build": {"system": "gradle", "project": f":{component_id}"},
            "paths": {"source": "src/main"},
        })
        generate_indexes(root)
        generate_dependency_graph(root)

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self._make_repository(self.root, "repo", "content", "Owns curriculum and lesson export.", ["export"])

    def tearDown(self):
        self.temp.cleanup()

    def test_route_objective_reuses_index_matching(self):
        matches = route_objective(self.root, "export lesson content")
        self.assertEqual("content", matches[0].component_id)

    def test_planning_questions_include_one_per_repository_plus_shared_fields(self):
        questions = planning_questions({"repo": route_objective(self.root, "export")})
        fields = [question["field"] for question in questions]
        self.assertEqual(
            ["components", "scope_boundary", "completion_conditions", "rollout_needs"], fields,
        )
        self.assertEqual("repo", questions[0]["repository"])

    def test_planning_questions_add_depends_on_only_for_multi_repository_drafts(self):
        second_root = self.root / "repo-b-root"
        self._make_repository(second_root, "repo-b", "content", "Owns repo-b content.", ["export"])
        questions = planning_questions({
            "repo": route_objective(self.root, "export"),
            "repo-b": route_objective(second_root, "export"),
        })
        fields = [question["field"] for question in questions]
        self.assertEqual(
            ["components", "depends_on", "components", "depends_on",
             "scope_boundary", "completion_conditions", "rollout_needs"],
            fields,
        )
        depends_on_questions = [question for question in questions if question["field"] == "depends_on"]
        self.assertEqual("repo", depends_on_questions[0]["repository"])
        self.assertEqual("", depends_on_questions[0]["suggested"])
        self.assertEqual("repo-b", depends_on_questions[1]["repository"])
        self.assertEqual("repo", depends_on_questions[1]["suggested"])
        self.assertIn("suggested: repo", depends_on_questions[1]["prompt"])

    def test_generates_valid_task_and_readme(self):
        written = generate_single_repository_workflow(
            self.root, "repo", "task-export", "Add CSV export.", "feature",
            ["content"], "No admin UI.", ["Export button works", "CSV matches schema"],
        )
        task_path, readme_path = written
        self.assertTrue(task_path.is_file())
        self.assertTrue(readme_path.is_file())
        self.assertEqual(ManifestState.VALID, validate_contract("task", task_path).state)
        document = load_yaml(task_path)
        self.assertEqual(["content"], document["scope"]["components"])
        self.assertIn("CSV matches schema", readme_path.read_text())

    def test_local_architecture_notes_render_in_their_own_readme_section(self):
        written = generate_single_repository_workflow(
            self.root, "repo", "task-export", "Add CSV export.", "feature",
            ["content"], "No admin UI.", ["Export button works"],
            local_architecture_notes=[".esc-ai/local-architecture/background-jobs.md"],
        )
        task_path, readme_path = written
        # Never written into the schema-validated task.yaml -- same precedent
        # architecture_doc_ids itself already sets (README-only).
        document = load_yaml(task_path)
        self.assertNotIn("local_architecture_notes", document)
        readme = readme_path.read_text()
        self.assertIn("## Local architecture notes (unreviewed)", readme)
        self.assertIn(".esc-ai/local-architecture/background-jobs.md", readme)

    def test_no_local_architecture_notes_section_when_none_given(self):
        written = generate_single_repository_workflow(
            self.root, "repo", "task-export", "Add CSV export.", "feature",
            ["content"], "No admin UI.", ["Export button works"],
        )
        _, readme_path = written
        self.assertNotIn("Local architecture notes", readme_path.read_text())

    def test_rejects_unresolvable_component_without_writing_anything(self):
        with self.assertRaisesRegex(ValueError, "not in the repository index"):
            generate_single_repository_workflow(
                self.root, "repo", "task-export", "Add export.", "feature",
                ["does-not-exist"], "", ["done"],
            )
        self.assertFalse((self.root / ".esc-ai" / "workflows" / "active" / "task-export").exists())

    def test_rejects_unknown_work_type(self):
        with self.assertRaisesRegex(ValueError, "work_type must be one of"):
            generate_single_repository_workflow(
                self.root, "repo", "task-export", "Add export.", "not-a-real-type",
                ["content"], "", ["done"],
            )

    def test_multi_repository_validates_everything_before_writing_anything(self):
        second = self.root / "second"
        self._make_repository(second, "repo-two", "api", "Owns the export API.", ["export"])
        registry = self.root / "registry.yaml"
        add_route(registry, "repositories", "repo", self.root)
        add_route(registry, "repositories", "repo-two", second)

        # repo-two references a component that doesn't exist -- nothing should be
        # written to *either* repository, including the valid one.
        with self.assertRaises(ValueError):
            generate_multi_repository_workflow(
                registry, "feature-export", "Add CSV export.", "feature",
                {
                    "repo": {"task_id": "task-contract", "components": ["content"], "completion_conditions": ["done"]},
                    "repo-two": {"task_id": "task-api", "components": ["missing"], "completion_conditions": ["done"],
                                 "depends_on": ["repo/task-contract"]},
                },
            )
        self.assertFalse((self.root / ".esc-ai" / "workflows" / "active" / "task-contract").exists())
        self.assertFalse((second / ".esc-ai" / "workflows" / "active" / "task-api").exists())

    def test_multi_repository_writes_cross_linked_tasks(self):
        second = self.root / "second-ok"
        self._make_repository(second, "repo-two", "api", "Owns the export API.", ["export"])
        registry = self.root / "registry-ok.yaml"
        add_route(registry, "repositories", "repo", self.root)
        add_route(registry, "repositories", "repo-two", second)

        written = generate_multi_repository_workflow(
            registry, "feature-export", "Add CSV export.", "feature",
            {
                "repo": {"task_id": "task-contract", "components": ["content"], "completion_conditions": ["done"]},
                "repo-two": {"task_id": "task-api", "components": ["api"], "completion_conditions": ["done"],
                             "depends_on": ["repo/task-contract"]},
            },
        )
        self.assertIn("repo", written)
        self.assertIn("repo-two", written)
        api_task = load_yaml(second / ".esc-ai/workflows/active/task-api/task.yaml")
        self.assertEqual("feature-export", api_task["task"]["initiative"]["id"])
        self.assertEqual(["repo/task-contract"], api_task["task"]["initiative"]["depends_on"])
        contract_task = load_yaml(self.root / ".esc-ai/workflows/active/task-contract/task.yaml")
        self.assertNotIn("depends_on", contract_task["task"]["initiative"])

    def test_multi_repository_rejects_a_two_task_cycle(self):
        second = self.root / "second-cycle"
        self._make_repository(second, "repo-two", "api", "Owns the export API.", ["export"])
        registry = self.root / "registry-cycle.yaml"
        add_route(registry, "repositories", "repo", self.root)
        add_route(registry, "repositories", "repo-two", second)

        with self.assertRaisesRegex(ValueError, "cycle"):
            generate_multi_repository_workflow(
                registry, "feature-export", "Add CSV export.", "feature",
                {
                    "repo": {"task_id": "task-contract", "components": ["content"], "completion_conditions": ["done"],
                             "depends_on": ["repo-two/task-api"]},
                    "repo-two": {"task_id": "task-api", "components": ["api"], "completion_conditions": ["done"],
                                 "depends_on": ["repo/task-contract"]},
                },
            )
        self.assertFalse((self.root / ".esc-ai" / "workflows" / "active" / "task-contract").exists())
        self.assertFalse((second / ".esc-ai" / "workflows" / "active" / "task-api").exists())

    def test_multi_repository_rejects_a_self_referencing_task(self):
        registry = self.root / "registry-self-cycle.yaml"
        add_route(registry, "repositories", "repo", self.root)

        with self.assertRaisesRegex(ValueError, "cycle"):
            generate_multi_repository_workflow(
                registry, "feature-export", "Add CSV export.", "feature",
                {
                    "repo": {"task_id": "task-contract", "components": ["content"], "completion_conditions": ["done"],
                             "depends_on": ["repo/task-contract"]},
                },
            )

    def test_multi_repository_accepts_a_branching_non_linear_graph(self):
        """
        Two independent tasks (backend, mobile) both depend on the same upstream
        task (contracts) but not on each other -- a real diamond-shaped graph, not
        the "chain to the previous entry" shape apply_plan happens to produce
        today. generate_multi_repository_workflow must accept this: it threads
        through whatever depends_on graph it's handed rather than inferring a
        linear order from dict iteration.
        """
        backend = self.root / "backend"
        self._make_repository(backend, "backend", "api", "Owns the backend API.", ["export"])
        mobile = self.root / "mobile"
        self._make_repository(mobile, "mobile", "app", "Owns the mobile client.", ["export"])
        registry = self.root / "registry-diamond.yaml"
        add_route(registry, "repositories", "repo", self.root)
        add_route(registry, "repositories", "backend", backend)
        add_route(registry, "repositories", "mobile", mobile)

        written = generate_multi_repository_workflow(
            registry, "feature-export", "Add CSV export.", "feature",
            {
                "repo": {"task_id": "task-contract", "components": ["content"], "completion_conditions": ["done"]},
                "backend": {"task_id": "task-backend", "components": ["api"], "completion_conditions": ["done"],
                            "depends_on": ["repo/task-contract"]},
                "mobile": {"task_id": "task-mobile", "components": ["app"], "completion_conditions": ["done"],
                           "depends_on": ["repo/task-contract"]},
            },
        )
        self.assertEqual({"repo", "backend", "mobile"}, set(written))
        backend_task = load_yaml(backend / ".esc-ai/workflows/active/task-backend/task.yaml")
        self.assertEqual(["repo/task-contract"], backend_task["task"]["initiative"]["depends_on"])
        mobile_task = load_yaml(mobile / ".esc-ai/workflows/active/task-mobile/task.yaml")
        self.assertEqual(["repo/task-contract"], mobile_task["task"]["initiative"]["depends_on"])

    def test_multi_repository_threads_local_architecture_notes_per_task(self):
        registry = self.root / "registry-notes.yaml"
        add_route(registry, "repositories", "repo", self.root)

        generate_multi_repository_workflow(
            registry, "feature-jobs", "Add background jobs.", "feature",
            {
                "repo": {
                    "task_id": "task-jobs", "components": ["content"], "completion_conditions": ["done"],
                    "local_architecture_notes": [".esc-ai/local-architecture/background-jobs.md"],
                },
            },
        )
        readme = (self.root / ".esc-ai/workflows/active/task-jobs/README.md").read_text()
        self.assertIn("## Local architecture notes (unreviewed)", readme)
        self.assertIn(".esc-ai/local-architecture/background-jobs.md", readme)

    def test_rejects_unsafe_task_id(self):
        with self.assertRaisesRegex(ValueError, "not safe"):
            generate_single_repository_workflow(
                self.root, "repo", "../escape", "Bad id.", "feature", ["content"], "", ["done"],
            )


if __name__ == "__main__":
    unittest.main()
