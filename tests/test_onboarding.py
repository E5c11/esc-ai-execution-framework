import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.contracts import validate_contract
from esc_exec.json_io import write_json
from esc_exec.manifests import generate_gradle_manifests
from esc_exec.model import ManifestState
from esc_exec.onboarding import analyze_repository, apply_onboarding_answers, import_project_profile
from esc_exec.registry import add_route
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

    def _register_architecture_framework(self, documents: list[dict], profile_doc_map: dict | None = None) -> Path:
        registry = self.root.parent / f"registry-{id(self)}.yaml"
        framework_root = self.root.parent / f"architecture-framework-{id(self)}"
        framework_root.mkdir(exist_ok=True)
        (framework_root / "index.json").write_text(
            json.dumps({"generated": "2026-01-01T00:00:00Z", "count": len(documents), "documents": documents}),
            encoding="utf-8",
        )
        (framework_root / "profile-doc-map.json").write_text(
            json.dumps(profile_doc_map or {"frameworks": {}, "targets": {}}), encoding="utf-8",
        )
        add_route(registry, "frameworks", "esc-ai-architecture-framework", framework_root)
        return registry

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

    def test_no_registry_path_means_no_architecture_question_or_suggestion(self):
        proposal = analyze_repository(self.root)
        self.assertEqual({}, proposal["profile_id_suggestions"])
        self.assertFalse(any(q["field"] == "frameworks" for q in proposal["semantic_questions"]))

    def test_registry_path_without_project_profile_asks_frameworks_question(self):
        registry = self._register_architecture_framework([])
        proposal = analyze_repository(self.root, registry)
        frameworks_questions = {q["component_id"] for q in proposal["semantic_questions"] if q["field"] == "frameworks"}
        self.assertEqual({"core-api", "feature"}, frameworks_questions)
        self.assertEqual({}, proposal["profile_id_suggestions"])

    def test_existing_project_profile_suggests_and_skips_the_question(self):
        (self.root / "context").mkdir()
        write_yaml(self.root / "context/project-profile.yaml", {
            "project": "sample", "platform": "mobile", "language": "kotlin",
            "architecture": "pragmatic-clean", "frameworks": {"network": "ktor"},
        })
        registry = self._register_architecture_framework(
            [], profile_doc_map={"frameworks": {"network": {"ktor": ["PLAT-MOB-HTTP"]}}, "targets": {}},
        )
        proposal = analyze_repository(self.root, registry)
        self.assertFalse(any(q["field"] == "frameworks" for q in proposal["semantic_questions"]))
        self.assertEqual(
            {"core-api": ["PLAT-MOB-HTTP"], "feature": ["PLAT-MOB-HTTP"]},
            proposal["profile_id_suggestions"],
        )

    def test_import_project_profile_reads_frameworks_and_targets(self):
        self.assertIsNone(import_project_profile(self.root))
        (self.root / "context").mkdir()
        write_yaml(self.root / "context/project-profile.yaml", {
            "project": "sample", "platform": "mobile", "language": "kotlin",
            "architecture": "pragmatic-clean", "targets": ["ios"],
            "frameworks": {"network": "ktor"},
        })
        imported = import_project_profile(self.root)
        self.assertEqual({"network": "ktor"}, imported["frameworks"])
        self.assertEqual(["ios"], imported["targets"])

    def test_apply_answers_writes_purpose_and_is_idempotent_on_reanalysis(self):
        proposal = analyze_repository(self.root)
        answers = {
            "core-api": {"purpose": "Owns the core API."},
            "feature": {"purpose": "Owns the feature."},
        }
        result = apply_onboarding_answers(self.root, proposal, answers)
        self.assertIn("esc-execution.yaml", result["written"])
        manifest = load_yaml(self.root / "core/api/esc-component.yaml")
        self.assertEqual("Owns the core API.", manifest["component"]["purpose"])

        # Re-analysis must not lose the authored purpose.
        second_proposal = analyze_repository(self.root)
        self.assertFalse(any(q["component_id"] == "core-api" for q in second_proposal["semantic_questions"]))
        actions = {entry["path"]: entry["action"] for entry in second_proposal["files"]}
        self.assertEqual("preserve", actions["core/api/esc-component.yaml"])

    def test_apply_answers_rejects_stale_proposal(self):
        proposal = analyze_repository(self.root)
        (self.root / "settings.gradle.kts").write_text(
            'rootProject.name = "renamed"\ninclude(":core:api")\ninclude(":feature")\n', encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "re-analyze"):
            apply_onboarding_answers(self.root, proposal, {})

    def test_apply_answers_suggests_profile_ids_from_explicit_answer(self):
        registry = self._register_architecture_framework(
            [{"id": "PLAT-MOB-HTTP", "type": "platform", "layer": "platforms",
              "path": "platforms/mobile/http.md", "platform": ["mobile"], "architecture": ["all"],
              "requires": [], "related": [], "tags": [], "status": ""}],
            profile_doc_map={"frameworks": {"network": {"ktor": ["PLAT-MOB-HTTP"]}}, "targets": {}},
        )
        proposal = analyze_repository(self.root, registry)
        answers = {
            "core-api": {"purpose": "Owns the core API.", "frameworks": {"network": "ktor"}},
            "feature": {"purpose": "Owns the feature.", "frameworks": {"network": "unmatched-lib"}},
        }
        result = apply_onboarding_answers(self.root, proposal, answers, registry)
        manifest = load_yaml(self.root / "core/api/esc-component.yaml")
        self.assertEqual(["PLAT-MOB-HTTP"], manifest["architecture"]["profile_ids"])
        self.assertIn("feature", result["empty_profile_id_suggestions"])
        self.assertNotIn("core-api", result["empty_profile_id_suggestions"])

    def test_apply_answers_surfaces_stub_documents(self):
        registry = self._register_architecture_framework(
            [{"id": "PLAT-MOB-HTTP", "type": "platform", "layer": "platforms",
              "path": "platforms/mobile/http.md", "platform": ["mobile"], "architecture": ["all"],
              "requires": [], "related": [], "tags": [], "status": "stub"}],
            profile_doc_map={"frameworks": {"network": {"ktor": ["PLAT-MOB-HTTP"]}}, "targets": {}},
        )
        proposal = analyze_repository(self.root, registry)
        answers = {
            "core-api": {"purpose": "Owns it.", "frameworks": {"network": "ktor"}},
            "feature": {"purpose": "Owns the feature."},
        }
        result = apply_onboarding_answers(self.root, proposal, answers, registry)
        self.assertEqual(["PLAT-MOB-HTTP"], result["stub_documents"]["core-api"])

    def test_apply_answers_generates_verification_and_architecture_profiles(self):
        proposal = analyze_repository(self.root)
        answers = {"core-api": {"purpose": "Owns it."}, "feature": {"purpose": "Owns the feature."}}
        result = apply_onboarding_answers(self.root, proposal, answers)
        self.assertTrue((self.root / "core/api/esc-verification-profile.yaml").is_file())
        self.assertTrue((self.root / "core/api/esc-architecture-profile.yaml").is_file())
        self.assertIn("core/api/esc-verification-profile.yaml", result["written"])
        self.assertIn("core/api/esc-architecture-profile.yaml", result["written"])

        # Re-applying must not blow up on already-existing profiles.
        second = apply_onboarding_answers(self.root, proposal, answers)
        self.assertNotIn("core/api/esc-verification-profile.yaml", second["written"])

    def test_apply_answers_leaves_indexes_and_dependency_graph_valid(self):
        # Regression: generate_gradle_verification_profile/generate_architecture_profile
        # write paths.verification_profile/architecture_profile back into a component's
        # manifest *after* it's first indexed. If the index isn't regenerated again after
        # that, it stays hashed against pre-profile-generation bytes and validate_indexes
        # reports STALE the instant onboarding finishes. esc-dependencies.json must also
        # actually get generated during apply, not left for a manual follow-up step.
        proposal = analyze_repository(self.root)
        answers = {"core-api": {"purpose": "Owns it."}, "feature": {"purpose": "Owns the feature."}}
        apply_onboarding_answers(self.root, proposal, answers)

        from esc_exec.indexing import validate_indexes
        from esc_exec.dependencies import validate_dependency_graph

        for result in validate_indexes(self.root):
            self.assertEqual(ManifestState.VALID, result.state, result.messages)
        self.assertTrue((self.root / "esc-dependencies.json").is_file())
        self.assertEqual(ManifestState.VALID, validate_dependency_graph(self.root).state)

    def test_apply_answers_bootstraps_workflow_inheritance(self):
        proposal = analyze_repository(self.root)
        answers = {"core-api": {"purpose": "Owns it."}, "feature": {"purpose": "Owns the feature."}}
        result = apply_onboarding_answers(self.root, proposal, answers)
        self.assertIn("INSTRUCTIONS.md", result["workflow_inheritance"]["created"])
        self.assertIn(".esc-ai/workflows/README.md", result["workflow_inheritance"]["created"])
        self.assertTrue((self.root / "INSTRUCTIONS.md").is_file())
        self.assertTrue((self.root / ".esc-ai/workflows/README.md").is_file())

        # Re-applying must report the files as existing, not recreate/overwrite them.
        second = apply_onboarding_answers(self.root, proposal, answers)
        self.assertEqual([], second["workflow_inheritance"]["created"])
        self.assertIn("INSTRUCTIONS.md", second["workflow_inheritance"]["existing"])


if __name__ == "__main__":
    unittest.main()
