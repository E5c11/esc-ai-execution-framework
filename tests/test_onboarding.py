from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.contracts import validate_contract
from esc_exec.json_io import write_json
from esc_exec.manifests import generate_gradle_manifests
from esc_exec.model import ManifestState
from esc_exec.onboarding import analyze_repository
from esc_exec.yaml_io import load_yaml, write_yaml


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "settings.gradle.kts").write_text(
            'rootProject.name = "sample"\ninclude(":core:api")\ninclude(":feature")\n',
            encoding="utf-8",
        )
        for component in (self.root / "core/api", self.root / "feature"):
            (component / "src/main/kotlin").mkdir(parents=True)
            (component / "build.gradle.kts").write_text("", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def _snapshot(self) -> set[str]:
        return {str(path.relative_to(self.root)) for path in self.root.rglob("*") if path.is_file()}

    def test_fresh_repository_is_all_create_with_questions(self):
        proposal = analyze_repository(self.root)
        self.assertEqual("sample", proposal["repository"]["id"])
        self.assertEqual("gradle-multi-project", proposal["repository"]["type"])
        actions = {entry["path"]: entry["action"] for entry in proposal["files"]}
        self.assertEqual("create", actions["esc-execution.yaml"])
        self.assertEqual("create", actions["core/api/esc-component.yaml"])
        self.assertEqual("create", actions["feature/esc-component.yaml"])
        self.assertEqual(
            {"core-api", "feature"},
            {question["component_id"] for question in proposal["semantic_questions"]},
        )
        self.assertEqual(
            {"instructions_file": False, "workflows_directory": False, "project_profile": False},
            proposal["existing_adoption"],
        )

    def test_analyze_does_not_write_anything(self):
        before = self._snapshot()
        analyze_repository(self.root)
        self.assertEqual(before, self._snapshot())

    def test_preserve_after_generation_and_authored_purpose(self):
        generate_gradle_manifests(self.root)
        manifest_path = self.root / "feature/esc-component.yaml"
        manifest = load_yaml(manifest_path)
        manifest["component"]["purpose"] = "Owns the sample feature."
        write_yaml(manifest_path, manifest)

        proposal = analyze_repository(self.root)
        actions = {entry["path"]: entry["action"] for entry in proposal["files"]}
        self.assertEqual("preserve", actions["esc-execution.yaml"])
        self.assertEqual("preserve", actions["feature/esc-component.yaml"])
        self.assertEqual("preserve", actions["core/api/esc-component.yaml"])
        question_components = {question["component_id"] for question in proposal["semantic_questions"]}
        self.assertNotIn("feature", question_components)
        self.assertIn("core-api", question_components)

    def test_detects_component_set_change_as_update(self):
        generate_gradle_manifests(self.root)
        (self.root / "extra").mkdir()
        (self.root / "extra/build.gradle.kts").write_text("", encoding="utf-8")
        (self.root / "settings.gradle.kts").write_text(
            'rootProject.name = "sample"\ninclude(":core:api")\ninclude(":feature")\ninclude(":extra")\n',
            encoding="utf-8",
        )
        proposal = analyze_repository(self.root)
        actions = {entry["path"]: entry["action"] for entry in proposal["files"]}
        self.assertEqual("update", actions["esc-execution.yaml"])
        self.assertEqual("create", actions["extra/esc-component.yaml"])

    def test_detects_removed_component_as_deprecate(self):
        generate_gradle_manifests(self.root)
        (self.root / "settings.gradle.kts").write_text(
            'rootProject.name = "sample"\ninclude(":core:api")\n', encoding="utf-8",
        )
        proposal = analyze_repository(self.root)
        deprecated = [entry for entry in proposal["files"] if entry["action"] == "deprecate"]
        self.assertEqual(["feature/esc-component.yaml"], [entry["path"] for entry in deprecated])

    def test_input_digest_is_stable_and_changes_with_input(self):
        first = analyze_repository(self.root)["input_digest"]
        second = analyze_repository(self.root)["input_digest"]
        self.assertEqual(first, second)
        (self.root / "settings.gradle.kts").write_text(
            'rootProject.name = "sample"\ninclude(":core:api")\ninclude(":feature")\ninclude(":extra")\n',
            encoding="utf-8",
        )
        (self.root / "extra").mkdir()
        (self.root / "extra/build.gradle.kts").write_text("", encoding="utf-8")
        third = analyze_repository(self.root)["input_digest"]
        self.assertNotEqual(first, third)

    def test_existing_adoption_signals_detected(self):
        (self.root / "INSTRUCTIONS.md").write_text("", encoding="utf-8")
        (self.root / ".esc-ai/workflows").mkdir(parents=True)
        (self.root / "context").mkdir()
        (self.root / "context/project-profile.yaml").write_text("", encoding="utf-8")
        proposal = analyze_repository(self.root)
        self.assertEqual(
            {"instructions_file": True, "workflows_directory": True, "project_profile": True},
            proposal["existing_adoption"],
        )

    def test_proposal_is_a_valid_contract(self):
        proposal = analyze_repository(self.root)
        output = self.root.parent / "proposal.json"
        write_json(output, proposal)
        self.assertEqual(ManifestState.VALID, validate_contract("onboarding-proposal", output).state)


if __name__ == "__main__":
    unittest.main()
