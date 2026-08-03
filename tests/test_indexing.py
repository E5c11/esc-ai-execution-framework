from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.indexing import generate_indexes, match_components, validate_indexes
from esc_exec.manifests import component_manifest_path, generate_gradle_manifests
from esc_exec.model import ManifestState
from esc_exec.yaml_io import load_yaml, write_yaml


class IndexingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "settings.gradle.kts").write_text(
            'rootProject.name = "sample"\ninclude(":auth")\ninclude(":content")\n',
            encoding="utf-8",
        )
        for component in ("auth", "content"):
            component_root = self.root / component
            source = component_root / "src/main/kotlin/com/example/backend" / component
            source.mkdir(parents=True)
            (component_root / "build.gradle.kts").write_text("", encoding="utf-8")
        (self.root / "auth/src/main/kotlin/com/example/backend/auth/AuthController.kt").write_text("", encoding="utf-8")
        (self.root / "auth/src/main/kotlin/com/example/backend/auth/PasswordResetService.kt").write_text("", encoding="utf-8")
        (self.root / "auth/src/main/kotlin/com/example/backend/auth/UserEntity.kt").write_text("", encoding="utf-8")
        (self.root / "content/src/main/kotlin/com/example/backend/content/LessonService.kt").write_text("", encoding="utf-8")
        generate_gradle_manifests(self.root)
        self._complete("auth", "Owns authentication and token security.", ["password-reset"])
        self._complete("content", "Owns lessons and content publishing.", ["learning-content"])

    def tearDown(self):
        self.temp.cleanup()

    def _complete(self, component: str, purpose: str, aliases: list[str]):
        path = component_manifest_path(self.root, component)
        data = load_yaml(path)
        data["component"]["purpose"] = purpose
        data["component"]["ownership"] = {
            "domains": [component],
            "concerns": ["http-api"],
        }
        data["component"]["routing"] = {"aliases": aliases}
        write_yaml(path, data)

    def test_generates_root_and_component_json_indexes(self):
        paths = generate_indexes(self.root)
        self.assertEqual(3, len(paths))
        content = (self.root / ".esc-ai/esc-index.json").read_text(encoding="utf-8")
        self.assertTrue(content.endswith("\n"))
        self.assertIn('"components": [', content)

    def test_repeated_generation_is_byte_identical(self):
        paths = generate_indexes(self.root)
        before = {path: path.read_bytes() for path in paths}
        generate_indexes(self.root)
        self.assertEqual(before, {path: path.read_bytes() for path in paths})

    def test_structural_change_makes_indexes_stale(self):
        generate_indexes(self.root)
        new_controller = self.root / "content/src/main/kotlin/com/example/backend/content/QuestionController.kt"
        new_controller.write_text("", encoding="utf-8")
        results = validate_indexes(self.root)
        self.assertTrue(any(result.state == ManifestState.STALE for result in results))

    def test_inventory_contains_entry_points_and_role_counts(self):
        generate_indexes(self.root)
        import json
        data = json.loads((self.root / ".esc-ai/components/auth/esc-index.json").read_text(encoding="utf-8"))
        self.assertEqual(1, data["structure"]["role_counts"]["entities"])
        self.assertEqual(
            ["src/main/kotlin/com/example/backend/auth/PasswordResetService.kt"],
            data["structure"]["entry_points"]["services"],
        )

    def test_matching_ranks_alias_and_entry_point_terms(self):
        generate_indexes(self.root)
        matches = match_components(self.root, "fix password reset expiry")
        self.assertEqual("auth", matches[0].component_id)
        self.assertGreater(matches[0].score, 0)

    def test_matching_normalizes_singular_and_plural_terms(self):
        generate_indexes(self.root)
        matches = match_components(self.root, "publish lesson")
        self.assertEqual("content", matches[0].component_id)

    def test_root_index_does_not_duplicate_component_search_details(self):
        generate_indexes(self.root)
        import json
        data = json.loads((self.root / ".esc-ai/esc-index.json").read_text(encoding="utf-8"))
        component = data["components"][0]
        self.assertNotIn("search_roots", component)
        self.assertNotIn("entry_point_terms", component["routing"])


class KmpComponentIndexingTests(unittest.TestCase):
    """
    Regression coverage for Kotlin Multiplatform components (src/commonMain/kotlin,
    src/androidMain/kotlin, ... rather than src/main/kotlin), found to produce empty
    search_roots/package_areas during the Phase 10 pilot against two real KMP repos.
    """

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "settings.gradle.kts").write_text(
            'rootProject.name = "sample"\ninclude(":feature:home")\n', encoding="utf-8",
        )
        common = self.root / "feature/home/src/commonMain/kotlin/com/example/home"
        android = self.root / "feature/home/src/androidMain/kotlin/com/example/home"
        common.mkdir(parents=True)
        android.mkdir(parents=True)
        (common / "HomeViewModel.kt").write_text("", encoding="utf-8")
        (android / "PlatformHomeBindings.kt").write_text("", encoding="utf-8")
        (self.root / "feature/home/build.gradle.kts").write_text("", encoding="utf-8")
        generate_gradle_manifests(self.root)
        manifest_path = component_manifest_path(self.root, "feature-home")
        manifest = load_yaml(manifest_path)
        manifest["component"]["purpose"] = "Owns the home feature."
        write_yaml(manifest_path, manifest)

    def tearDown(self):
        self.temp.cleanup()

    def test_search_roots_include_every_source_set(self):
        generate_indexes(self.root)
        import json
        data = json.loads((self.root / ".esc-ai/components/feature-home/esc-index.json").read_text(encoding="utf-8"))
        self.assertEqual(
            {"feature/home/src/commonMain/kotlin", "feature/home/src/androidMain/kotlin"},
            set(data["search_roots"]),
        )

    def test_package_areas_span_every_source_set(self):
        generate_indexes(self.root)
        import json
        data = json.loads((self.root / ".esc-ai/components/feature-home/esc-index.json").read_text(encoding="utf-8"))
        self.assertEqual(["com/example/home"], data["structure"]["package_areas"])

    def test_component_index_embeds_resolved_testing_facts_for_matched_platform_only(self):
        """
        plan/done/manifest-testing-facts-and-documentation-obligation.md: this
        is the real integration point -- `_prompt` (every adapter) tells the
        agent to read exactly this file for each declared component, unlike
        `build_instruction_bundle`'s levels, which no adapter's prompt actually
        consumes.
        """
        from esc_exec.manifests import repository_manifest_path
        repository_path = repository_manifest_path(self.root)
        repository = load_yaml(repository_path)
        repository["testing"] = {
            "common": {"unit_framework": "kotlin-test", "lint_tools": ["detekt"]},
            "platforms": {
                "android": {"source_sets": ["androidMain"], "ui_testing": {"framework": "ai-adb-compose-testtag"}},
                "ios": {"source_sets": ["iosMain"], "ui_testing": {"framework": "xctest"}},
            },
        }
        write_yaml(repository_path, repository)
        generate_indexes(self.root)
        import json
        data = json.loads((self.root / ".esc-ai/components/feature-home/esc-index.json").read_text(encoding="utf-8"))
        self.assertEqual("kotlin-test", data["testing"]["common"]["unit_framework"])
        # This fixture only has commonMain/androidMain -- ios must not appear,
        # since this component has no iosMain/nativeMain source set at all.
        self.assertEqual({"android"}, set(data["testing"]["platforms"]))
        self.assertEqual("ai-adb-compose-testtag", data["testing"]["platforms"]["android"]["ui_testing"]["framework"])

    def test_component_index_testing_absorbs_source_set_naming_drift(self):
        """Real finding: most ampm-kmp components use `iosMain`, but some use
        `nativeMain` for the same conceptual target -- source_sets is what
        absorbs this, not the declared platform name itself."""
        (self.root / "feature/home/src/nativeMain/kotlin/com/example/home").mkdir(parents=True)
        generate_gradle_manifests(self.root)  # re-detect paths to pick up the new source set
        from esc_exec.manifests import repository_manifest_path
        repository_path = repository_manifest_path(self.root)
        repository = load_yaml(repository_path)
        repository["testing"] = {
            "platforms": {"ios": {"source_sets": ["iosMain", "nativeMain"], "ui_testing": {"framework": "xctest"}}},
        }
        write_yaml(repository_path, repository)
        generate_indexes(self.root)
        import json
        data = json.loads((self.root / ".esc-ai/components/feature-home/esc-index.json").read_text(encoding="utf-8"))
        self.assertEqual({"ios"}, set(data["testing"]["platforms"]))

    def test_component_index_has_no_testing_key_when_not_declared(self):
        generate_indexes(self.root)
        import json
        data = json.loads((self.root / ".esc-ai/components/feature-home/esc-index.json").read_text(encoding="utf-8"))
        self.assertNotIn("testing", data)

    def test_root_index_embeds_documentation_when_declared(self):
        from esc_exec.manifests import repository_manifest_path
        repository_path = repository_manifest_path(self.root)
        repository = load_yaml(repository_path)
        repository["documentation"] = {"location": "wiki/", "convention": "One page per feature."}
        write_yaml(repository_path, repository)
        generate_indexes(self.root)
        import json
        root_index = json.loads((self.root / ".esc-ai/esc-index.json").read_text(encoding="utf-8"))
        self.assertEqual({"location": "wiki/", "convention": "One page per feature."}, root_index["documentation"])

    def test_root_index_has_no_documentation_key_when_not_declared(self):
        generate_indexes(self.root)
        import json
        root_index = json.loads((self.root / ".esc-ai/esc-index.json").read_text(encoding="utf-8"))
        self.assertNotIn("documentation", root_index)


if __name__ == "__main__":
    unittest.main()
