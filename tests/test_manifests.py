from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from esc_exec.manifests import (
    component_manifest_path, generate_gradle_manifests, generate_npm_manifests,
    repository_manifest_path, validate_component, validate_repository,
)
from esc_exec.model import ManifestState
from esc_exec.registry import RENAMED_FRAMEWORK_IDS, add_route
from esc_exec.yaml_io import load_yaml, write_yaml


class ManifestTests(unittest.TestCase):
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

    def test_generation_discovers_nested_gradle_components(self):
        generated = generate_gradle_manifests(self.root)
        self.assertEqual(3, len(generated))
        repository = load_yaml(repository_manifest_path(self.root))
        self.assertEqual("sample", repository["repository"]["id"])
        self.assertEqual(
            [".esc-ai/components/core-api/esc-component.yaml", ".esc-ai/components/feature/esc-component.yaml"],
            [item["manifest"] for item in repository["components"]],
        )

    def test_build_project_reflects_real_gradle_path_for_a_remapped_module(self):
        # Regression: build.project used to be reconstructed from the component's
        # directory (":" + relative.parts joined), which only matches the real
        # Gradle project path by convention -- projectDir remapping breaks that
        # convention on purpose (see gradle.py's PROJECT_DIR_RE), so the
        # reconstruction silently produced the wrong project path, which
        # dependencies.py's build_dependency_graph then can't match against a real
        # `project(":the-real-path")` declaration anywhere else in the repo.
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "settings.gradle.kts").write_text(
                'rootProject.name = "arrow-errors"\n'
                'include(":arrow-errors-core")\n'
                'project(":arrow-errors-core").projectDir = file("error-core")\n',
                encoding="utf-8",
            )
            (root / "error-core/src/main/kotlin").mkdir(parents=True)
            generate_gradle_manifests(root)
            manifest = load_yaml(component_manifest_path(root, "arrow-errors-core"))
            self.assertEqual(":arrow-errors-core", manifest["build"]["project"])
            self.assertEqual("error-core", manifest["component"]["path"])

    def test_override_components_are_used_instead_of_fresh_detection(self):
        generated = generate_gradle_manifests(
            self.root, repository_id="sample", components=[("core-api", Path("core/api"))],
        )
        self.assertEqual(2, len(generated))
        repository = load_yaml(repository_manifest_path(self.root))
        self.assertEqual(
            [".esc-ai/components/core-api/esc-component.yaml"],
            [item["manifest"] for item in repository["components"]],
        )

    def test_generated_component_is_incomplete_until_purpose_is_authored(self):
        generate_gradle_manifests(self.root)
        results = validate_repository(self.root)
        self.assertTrue(any(result.state == ManifestState.INCOMPLETE for result in results))

    def test_regeneration_preserves_human_purpose(self):
        generate_gradle_manifests(self.root)
        manifest_path = component_manifest_path(self.root, "feature")
        manifest = load_yaml(manifest_path)
        manifest["component"]["purpose"] = "Owns the sample feature."
        write_yaml(manifest_path, manifest)
        generate_gradle_manifests(self.root)
        self.assertEqual("Owns the sample feature.", load_yaml(manifest_path)["component"]["purpose"])

    def test_detects_undeclared_component_as_stale(self):
        generate_gradle_manifests(self.root)
        repository_path = repository_manifest_path(self.root)
        repository = load_yaml(repository_path)
        repository["components"] = repository["components"][:1]
        write_yaml(repository_path, repository)
        results = validate_repository(self.root)
        self.assertEqual(ManifestState.STALE, results[0].state)

    def test_renamed_framework_reference_is_stale(self):
        generate_gradle_manifests(self.root)
        repository_path = repository_manifest_path(self.root)
        repository = load_yaml(repository_path)
        old_id = next(iter(RENAMED_FRAMEWORK_IDS))
        repository["frameworks"] = {old_id: "1.0"}
        write_yaml(repository_path, repository)
        results = validate_repository(self.root)
        self.assertEqual(ManifestState.STALE, results[0].state)
        self.assertTrue(any("renamed framework ID" in message for message in results[0].messages))

    def test_matching_framework_major_version_is_valid(self):
        generate_gradle_manifests(self.root)
        registry = self.root / "registry.yaml"
        framework_dir = self.root / "framework-checkout"
        framework_dir.mkdir()
        write_yaml(framework_dir / "esc-framework.yaml", {
            "schema_version": 1,
            "framework": {"id": "esc-ai-execution-framework", "major_version": 1},
        })
        add_route(registry, "frameworks", "esc-ai-execution-framework", framework_dir)
        repository_path = repository_manifest_path(self.root)
        repository = load_yaml(repository_path)
        repository["frameworks"] = {"esc-ai-execution-framework": "1"}
        write_yaml(repository_path, repository)
        results = validate_repository(self.root, registry)
        self.assertNotEqual(ManifestState.INVALID, results[0].state)
        self.assertFalse(any("major version" in message for message in results[0].messages))

    def test_mismatched_framework_major_version_is_stale(self):
        generate_gradle_manifests(self.root)
        registry = self.root / "registry.yaml"
        framework_dir = self.root / "framework-checkout"
        framework_dir.mkdir()
        write_yaml(framework_dir / "esc-framework.yaml", {
            "schema_version": 1,
            "framework": {"id": "esc-ai-execution-framework", "major_version": 2},
        })
        add_route(registry, "frameworks", "esc-ai-execution-framework", framework_dir)
        repository_path = repository_manifest_path(self.root)
        repository = load_yaml(repository_path)
        repository["frameworks"] = {"esc-ai-execution-framework": "1"}
        write_yaml(repository_path, repository)
        results = validate_repository(self.root, registry)
        self.assertEqual(ManifestState.STALE, results[0].state)
        self.assertTrue(any("major version 1" in message and "major version 2" in message for message in results[0].messages))

    def test_missing_framework_descriptor_is_invalid(self):
        generate_gradle_manifests(self.root)
        registry = self.root / "registry.yaml"
        framework_dir = self.root / "framework-checkout"
        framework_dir.mkdir()
        add_route(registry, "frameworks", "esc-ai-execution-framework", framework_dir)
        repository_path = repository_manifest_path(self.root)
        repository = load_yaml(repository_path)
        repository["frameworks"] = {"esc-ai-execution-framework": "1"}
        write_yaml(repository_path, repository)
        results = validate_repository(self.root, registry)
        self.assertEqual(ManifestState.INVALID, results[0].state)
        self.assertTrue(any("could not read" in message for message in results[0].messages))

    def test_valid_architecture_selector_is_accepted(self):
        generate_gradle_manifests(self.root)
        repository_path = repository_manifest_path(self.root)
        repository = load_yaml(repository_path)
        repository["architecture"] = {"profile_ids": ["ORCH-BE-FEAT"]}
        write_yaml(repository_path, repository)
        results = validate_repository(self.root)
        self.assertNotEqual(ManifestState.INVALID, results[0].state)

    def test_invalid_architecture_selector_shape_is_invalid(self):
        generate_gradle_manifests(self.root)
        repository_path = repository_manifest_path(self.root)
        repository = load_yaml(repository_path)
        repository["architecture"] = {"profile_ids": []}
        write_yaml(repository_path, repository)
        results = validate_repository(self.root)
        self.assertEqual(ManifestState.INVALID, results[0].state)
        self.assertTrue(any("profile_ids" in message for message in results[0].messages))

    def test_worktree_inherit_absent_is_valid(self):
        generate_gradle_manifests(self.root)
        results = validate_repository(self.root)
        self.assertNotEqual(ManifestState.INVALID, results[0].state)

    def test_worktree_inherit_valid_list_is_accepted(self):
        generate_gradle_manifests(self.root)
        repository_path = repository_manifest_path(self.root)
        repository = load_yaml(repository_path)
        repository["worktree_inherit"] = ["local.properties", ".env"]
        write_yaml(repository_path, repository)
        results = validate_repository(self.root)
        self.assertNotEqual(ManifestState.INVALID, results[0].state)

    def test_worktree_inherit_non_list_is_invalid(self):
        generate_gradle_manifests(self.root)
        repository_path = repository_manifest_path(self.root)
        repository = load_yaml(repository_path)
        repository["worktree_inherit"] = "local.properties"
        write_yaml(repository_path, repository)
        results = validate_repository(self.root)
        self.assertEqual(ManifestState.INVALID, results[0].state)
        self.assertTrue(any("worktree_inherit" in message for message in results[0].messages))

    def test_worktree_inherit_with_blank_entry_is_invalid(self):
        generate_gradle_manifests(self.root)
        repository_path = repository_manifest_path(self.root)
        repository = load_yaml(repository_path)
        repository["worktree_inherit"] = ["local.properties", "  "]
        write_yaml(repository_path, repository)
        results = validate_repository(self.root)
        self.assertEqual(ManifestState.INVALID, results[0].state)
        self.assertTrue(any("worktree_inherit" in message for message in results[0].messages))

    def test_schema_documents_are_valid_yaml_mappings(self):
        schemas = Path(__file__).parents[1] / "schemas"
        for schema in schemas.glob("*.schema.yaml"):
            self.assertIn("$schema", load_yaml(schema))
        for schema in schemas.glob("*.schema.json"):
            self.assertIn("$schema", json.loads(schema.read_text(encoding="utf-8")))


class NpmManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "package.json").write_text(json.dumps({"name": "garage-triage"}), encoding="utf-8")
        (self.root / "src").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_override_components_are_used_instead_of_fresh_detection(self):
        generated = generate_npm_manifests(
            self.root, repository_id="garage-triage", components=[("app", Path("packages/app"))],
        )
        self.assertEqual(2, len(generated))
        repository = load_yaml(repository_manifest_path(self.root))
        self.assertEqual(
            [".esc-ai/components/app/esc-component.yaml"], [item["manifest"] for item in repository["components"]],
        )

    def test_generation_writes_repository_and_component_manifests(self):
        generated = generate_npm_manifests(self.root)
        self.assertEqual(2, len(generated))
        repository = load_yaml(repository_manifest_path(self.root))
        self.assertEqual("garage-triage", repository["repository"]["id"])
        self.assertEqual("npm-package", repository["repository"]["type"])
        self.assertEqual(
            [".esc-ai/components/garage-triage/esc-component.yaml"],
            [item["manifest"] for item in repository["components"]],
        )
        component = load_yaml(component_manifest_path(self.root, "garage-triage"))
        self.assertEqual("npm", component["build"]["system"])
        self.assertEqual("src", component["paths"]["source"])

    def test_generated_component_is_valid_once_purpose_is_authored(self):
        generate_npm_manifests(self.root)
        manifest_path = component_manifest_path(self.root, "garage-triage")
        manifest = load_yaml(manifest_path)
        manifest["component"]["purpose"] = "Owns the garage fault-triage app."
        write_yaml(manifest_path, manifest)
        result = validate_component(self.root, manifest_path, expected_id="garage-triage")
        self.assertEqual(ManifestState.VALID, result.state)


class ValidateComponentBuildSystemTests(unittest.TestCase):
    def test_unknown_build_system_is_invalid(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            manifest_path = root / "component.yaml"
            write_yaml(manifest_path, {
                "schema_version": 1,
                "component": {"id": "x", "type": "x", "path": ".", "purpose": "x"},
                "build": {"system": "maven"},
            })
            result = validate_component(root, manifest_path, expected_id="x")
            self.assertEqual(ManifestState.INVALID, result.state)
            self.assertTrue(any("build.system" in message for message in result.messages))


if __name__ == "__main__":
    unittest.main()
