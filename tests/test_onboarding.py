import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.contracts import validate_contract
from esc_exec.json_io import write_json
from esc_exec.manifests import component_manifest_path, generate_gradle_manifests
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
        self.assertEqual("create", actions[".esc-ai/esc-execution.yaml"])
        self.assertEqual("create", actions[".esc-ai/components/core-api/esc-component.yaml"])
        self.assertEqual("create", actions[".esc-ai/components/feature/esc-component.yaml"])
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
        manifest_path = component_manifest_path(self.root, "feature")
        manifest = load_yaml(manifest_path)
        manifest["component"]["purpose"] = "Owns the sample feature."
        write_yaml(manifest_path, manifest)

        proposal = analyze_repository(self.root)
        actions = {entry["path"]: entry["action"] for entry in proposal["files"]}
        self.assertEqual("preserve", actions[".esc-ai/esc-execution.yaml"])
        self.assertEqual("preserve", actions[".esc-ai/components/feature/esc-component.yaml"])
        self.assertEqual("preserve", actions[".esc-ai/components/core-api/esc-component.yaml"])
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
        self.assertEqual("update", actions[".esc-ai/esc-execution.yaml"])
        self.assertEqual("create", actions[".esc-ai/components/extra/esc-component.yaml"])

    def test_detects_removed_component_as_deprecate(self):
        generate_gradle_manifests(self.root)
        (self.root / "settings.gradle.kts").write_text(
            'rootProject.name = "sample"\ninclude(":core:api")\n', encoding="utf-8",
        )
        proposal = analyze_repository(self.root)
        deprecated = [entry for entry in proposal["files"] if entry["action"] == "deprecate"]
        self.assertEqual([".esc-ai/components/feature/esc-component.yaml"], [entry["path"] for entry in deprecated])

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
        (self.root / ".esc-ai").mkdir(parents=True)
        (self.root / ".esc-ai/INSTRUCTIONS.md").write_text("", encoding="utf-8")
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

    def test_detected_gradle_dependency_suggests_and_skips_the_question_tier1(self):
        # No context/project-profile.yaml, no explicit answer -- Tier 1 static
        # detection (plan/onboarding-answer-detection-and-suggestion.md) should find
        # `core-api`'s real dependency and skip asking it, while `feature` (empty
        # build.gradle.kts, per setUp) still gets asked normally.
        (self.root / "core/api/build.gradle.kts").write_text(
            'dependencies {\n    implementation("io.ktor:ktor-client-core:2.3.0")\n}\n', encoding="utf-8",
        )
        registry = self._register_architecture_framework(
            [], profile_doc_map={"frameworks": {"network": {"ktor": ["PLAT-MOB-HTTP"]}}, "targets": {}},
        )
        proposal = analyze_repository(self.root, registry)
        frameworks_questions = {q["component_id"] for q in proposal["semantic_questions"] if q["field"] == "frameworks"}
        self.assertEqual({"feature"}, frameworks_questions)
        self.assertEqual({"core-api": ["PLAT-MOB-HTTP"]}, proposal["profile_id_suggestions"])

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
        self.assertIn(".esc-ai/esc-execution.yaml", result["written"])
        manifest = load_yaml(component_manifest_path(self.root, "core-api"))
        self.assertEqual("Owns the core API.", manifest["component"]["purpose"])

        # Re-analysis must not lose the authored purpose.
        second_proposal = analyze_repository(self.root)
        self.assertFalse(any(q["component_id"] == "core-api" for q in second_proposal["semantic_questions"]))
        actions = {entry["path"]: entry["action"] for entry in second_proposal["files"]}
        self.assertEqual("preserve", actions[".esc-ai/components/core-api/esc-component.yaml"])

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
        manifest = load_yaml(component_manifest_path(self.root, "core-api"))
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
        component_dir = component_manifest_path(self.root, "core-api").parent
        self.assertTrue((component_dir / "esc-verification-profile.yaml").is_file())
        self.assertTrue((component_dir / "esc-architecture-profile.yaml").is_file())
        self.assertIn(".esc-ai/components/core-api/esc-verification-profile.yaml", result["written"])
        self.assertIn(".esc-ai/components/core-api/esc-architecture-profile.yaml", result["written"])

        # Re-applying must not blow up on already-existing profiles.
        second = apply_onboarding_answers(self.root, proposal, answers)
        self.assertNotIn(".esc-ai/components/core-api/esc-verification-profile.yaml", second["written"])

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
        self.assertTrue((self.root / ".esc-ai/esc-dependencies.json").is_file())
        self.assertEqual(ManifestState.VALID, validate_dependency_graph(self.root).state)

    def test_apply_answers_bootstraps_workflow_inheritance(self):
        proposal = analyze_repository(self.root)
        answers = {"core-api": {"purpose": "Owns it."}, "feature": {"purpose": "Owns the feature."}}
        result = apply_onboarding_answers(self.root, proposal, answers)
        self.assertIn(".esc-ai/INSTRUCTIONS.md", result["workflow_inheritance"]["created"])
        self.assertIn(".esc-ai/workflows/README.md", result["workflow_inheritance"]["created"])
        self.assertTrue((self.root / ".esc-ai/INSTRUCTIONS.md").is_file())
        self.assertTrue((self.root / ".esc-ai/workflows/README.md").is_file())

        # Re-applying must report the files as existing, not recreate/overwrite them.
        second = apply_onboarding_answers(self.root, proposal, answers)
        self.assertEqual([], second["workflow_inheritance"]["created"])
        self.assertIn(".esc-ai/INSTRUCTIONS.md", second["workflow_inheritance"]["existing"])


class NpmOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "package.json").write_text(json.dumps({"name": "garage-triage"}), encoding="utf-8")
        (self.root / "src").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_fresh_npm_repository_is_all_create_with_questions(self):
        proposal = analyze_repository(self.root)
        self.assertEqual("garage-triage", proposal["repository"]["id"])
        self.assertEqual("npm-package", proposal["repository"]["type"])
        actions = {entry["path"]: entry["action"] for entry in proposal["files"]}
        self.assertEqual("create", actions[".esc-ai/esc-execution.yaml"])
        self.assertEqual(
            "create", actions[".esc-ai/components/garage-triage/esc-component.yaml"],
        )
        self.assertEqual(
            {"garage-triage"}, {question["component_id"] for question in proposal["semantic_questions"]},
        )

    def test_apply_answers_dispatches_to_npm_generator_and_skips_verification_profile(self):
        proposal = analyze_repository(self.root)
        answers = {"garage-triage": {"purpose": "Turns free-text complaints into structured tickets."}}
        result = apply_onboarding_answers(self.root, proposal, answers)
        self.assertIn(".esc-ai/esc-execution.yaml", result["written"])
        manifest_path = component_manifest_path(self.root, "garage-triage")
        manifest = load_yaml(manifest_path)
        self.assertEqual("npm", manifest["build"]["system"])
        self.assertEqual(
            "Turns free-text complaints into structured tickets.", manifest["component"]["purpose"],
        )
        self.assertFalse((manifest_path.parent / "esc-verification-profile.yaml").exists())


class SingleModuleGradleOnboardingTests(unittest.TestCase):
    """
    Regression coverage for a repository with no include(...) subprojects at all
    (e.g. a small published library like ampm-contracts) -- found during the
    Phase 10 multi-repository pilot to detect zero components and so be
    permanently unonboardable for dependency/impact tracking.
    """

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "settings.gradle.kts").write_text(
            'rootProject.name = "solo"\n', encoding="utf-8",
        )
        (self.root / "src/main/kotlin").mkdir(parents=True)
        (self.root / "build.gradle.kts").write_text("", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_root_project_is_detected_as_sole_component(self):
        proposal = analyze_repository(self.root)
        self.assertEqual("solo", proposal["repository"]["id"])
        self.assertEqual(
            {"solo"}, {question["component_id"] for question in proposal["semantic_questions"]},
        )

    def test_apply_and_validate_succeed_for_root_only_repository(self):
        proposal = analyze_repository(self.root)
        answers = {"solo": {"purpose": "Owns the solo library."}}
        result = apply_onboarding_answers(self.root, proposal, answers)
        manifest = load_yaml(component_manifest_path(self.root, "solo"))
        self.assertEqual(".", manifest["component"]["path"])
        self.assertEqual(":", manifest["build"]["project"])
        self.assertEqual("Owns the solo library.", manifest["component"]["purpose"])

        from esc_exec.indexing import validate_indexes
        from esc_exec.dependencies import validate_dependency_graph

        for validation in validate_indexes(self.root):
            self.assertEqual(ManifestState.VALID, validation.state, validation.messages)
        self.assertEqual(ManifestState.VALID, validate_dependency_graph(self.root).state)
        self.assertIn(".esc-ai/esc-dependencies.json", result["written"])


class ComponentExclusionTests(unittest.TestCase):
    """
    plan/active/generic-multi-component-detection.md design section 6 --
    excluded_components persistence.
    """

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

    def test_excluded_component_gets_no_manifest_and_is_persisted(self):
        proposal = analyze_repository(self.root)
        answers = {
            "core-api": {"purpose": "Owns the core API."},
            "feature": {"purpose": "Owns the feature."},
        }
        apply_onboarding_answers(self.root, proposal, answers, excluded_component_ids=["feature"])

        self.assertFalse(component_manifest_path(self.root, "feature").is_file())
        repository = load_yaml(self.root / ".esc-ai" / "esc-execution.yaml")
        self.assertEqual(["feature"], repository["excluded_components"])
        self.assertEqual(["core-api"], [item["id"] for item in repository["components"]])

    def test_excluded_component_is_never_reoffered_on_a_later_analyze(self):
        proposal = analyze_repository(self.root)
        answers = {
            "core-api": {"purpose": "Owns the core API."},
            "feature": {"purpose": "Owns the feature."},
        }
        apply_onboarding_answers(self.root, proposal, answers, excluded_component_ids=["feature"])

        second_proposal = analyze_repository(self.root)
        self.assertEqual({"core-api"}, {component["id"] for component in second_proposal["components"]})
        actions = {entry["path"]: entry["action"] for entry in second_proposal["files"]}
        self.assertNotIn(".esc-ai/components/feature/esc-component.yaml", actions)


class ResolvedComponentPersistenceTests(unittest.TestCase):
    """
    plan/active/generic-multi-component-detection.md design sections 3-5 --
    Tier 2 AI-resolved module identity, persisted so a later analyze/apply picks
    it up without re-invoking AI.
    """

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "settings.gradle.kts").write_text(
            'rootProject.name = "arrow-errors"\n'
            'include(":arrow-errors-core")\n'
            'project(":arrow-errors-core").projectDir = file("error-core")\n',
            encoding="utf-8",
        )
        (self.root / "error-core/src/main/kotlin").mkdir(parents=True)
        (self.root / "error-core/build.gradle.kts").write_text("", encoding="utf-8")
        # A genuinely unresolvable module -- no projectDir remap tells us where
        # it really lives, only a (fake) Tier 2 AI answer would.
        (self.root / "settings.gradle.kts").write_text(
            (self.root / "settings.gradle.kts").read_text(encoding="utf-8") + 'include(":arrow-errors-ghost")\n',
            encoding="utf-8",
        )
        (self.root / "ghost-dir").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_extra_resolved_components_appear_in_analysis(self):
        proposal = analyze_repository(
            self.root, extra_resolved_components={":arrow-errors-ghost": "ghost-dir"},
        )
        self.assertEqual(
            {"arrow-errors-core", "arrow-errors-ghost"}, {c["id"] for c in proposal["components"]},
        )

    def test_apply_persists_resolution_and_writes_its_manifest(self):
        proposal = analyze_repository(self.root, extra_resolved_components={":arrow-errors-ghost": "ghost-dir"})
        answers = {
            "arrow-errors-core": {"purpose": "Core errors."},
            "arrow-errors-ghost": {"purpose": "The ghost module."},
        }
        apply_onboarding_answers(
            self.root, proposal, answers, resolved_components={":arrow-errors-ghost": "ghost-dir"},
        )
        manifest = load_yaml(component_manifest_path(self.root, "arrow-errors-ghost"))
        self.assertEqual("ghost-dir", manifest["component"]["path"])
        self.assertEqual("The ghost module.", manifest["component"]["purpose"])
        repository = load_yaml(self.root / ".esc-ai" / "esc-execution.yaml")
        self.assertEqual({":arrow-errors-ghost": "ghost-dir"}, repository["resolved_components"])

    def test_later_analyze_picks_up_persisted_resolution_without_reasking(self):
        proposal = analyze_repository(self.root, extra_resolved_components={":arrow-errors-ghost": "ghost-dir"})
        answers = {
            "arrow-errors-core": {"purpose": "Core errors."},
            "arrow-errors-ghost": {"purpose": "The ghost module."},
        }
        apply_onboarding_answers(
            self.root, proposal, answers, resolved_components={":arrow-errors-ghost": "ghost-dir"},
        )
        # No extra_resolved_components passed this time -- the persisted manifest
        # alone must be enough.
        second_proposal = analyze_repository(self.root)
        self.assertEqual(
            {"arrow-errors-core", "arrow-errors-ghost"}, {c["id"] for c in second_proposal["components"]},
        )
        actions = {entry["path"]: entry["action"] for entry in second_proposal["files"]}
        self.assertEqual(
            "preserve", actions[".esc-ai/components/arrow-errors-ghost/esc-component.yaml"],
        )

    def test_resolution_pointing_at_nonexistent_directory_is_ignored(self):
        proposal = analyze_repository(self.root, extra_resolved_components={":arrow-errors-ghost": "nope"})
        self.assertEqual({"arrow-errors-core"}, {c["id"] for c in proposal["components"]})


if __name__ == "__main__":
    unittest.main()
