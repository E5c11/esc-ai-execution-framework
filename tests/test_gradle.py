from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.gradle import (
    component_structure, detect_gradle_repository, gradle_project_paths, unresolved_gradle_includes,
)


class DetectGradleRepositoryTests(unittest.TestCase):
    def test_single_module_repository_detects_root_as_sole_component(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "settings.gradle.kts").write_text('rootProject.name = "solo"\n', encoding="utf-8")
            (root / "build.gradle.kts").write_text("", encoding="utf-8")
            repository_id, components = detect_gradle_repository(root)
            self.assertEqual("solo", repository_id)
            self.assertEqual([("solo", Path("."))], components)

    def test_single_module_repository_without_build_file_has_no_components(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "settings.gradle.kts").write_text('rootProject.name = "empty"\n', encoding="utf-8")
            repository_id, components = detect_gradle_repository(root)
            self.assertEqual("empty", repository_id)
            self.assertEqual([], components)

    def test_multi_module_repository_unaffected(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "settings.gradle.kts").write_text(
                'rootProject.name = "sample"\ninclude(":core:api")\ninclude(":feature")\n',
                encoding="utf-8",
            )
            (root / "core/api").mkdir(parents=True)
            (root / "feature").mkdir(parents=True)
            _, components = detect_gradle_repository(root)
            self.assertEqual(
                {("core-api", Path("core/api")), ("feature", Path("feature"))}, set(components),
            )

    def test_single_call_with_multiple_comma_separated_modules_is_detected(self):
        # Regression: a real repository onboarded with this exact shape (the most
        # common Kotlin DSL pattern for a multi-module project) previously matched
        # zero components at all -- INCLUDE_RE required exactly one bare quoted
        # argument per include(...) call -- and silently fell back to treating the
        # whole repository as a single component named after rootProject.name,
        # swallowing every real module.
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "settings.gradle.kts").write_text(
                'rootProject.name = "sample"\ninclude(":core", ":feature", ":app")\n',
                encoding="utf-8",
            )
            for module in ("core", "feature", "app"):
                (root / module).mkdir()
            _, components = detect_gradle_repository(root)
            self.assertEqual(
                {("core", Path("core")), ("feature", Path("feature")), ("app", Path("app"))}, set(components),
            )

    def test_groovy_style_include_without_parens_is_detected(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "settings.gradle").write_text(
                "rootProject.name = 'sample'\ninclude ':core', ':feature'\n", encoding="utf-8",
            )
            for module in ("core", "feature"):
                (root / module).mkdir()
            _, components = detect_gradle_repository(root)
            self.assertEqual({("core", Path("core")), ("feature", Path("feature"))}, set(components))

    def test_project_dir_remap_resolves_module_at_its_real_directory(self):
        # Regression: found against a real multi-module repository where every
        # module remapped its projectDir to a shorter folder name than its
        # namespaced Gradle project path -- only the one module with no remap
        # (whose folder happened to match its colon path) was ever detected;
        # every remapped module silently vanished.
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "settings.gradle.kts").write_text(
                'rootProject.name = "arrow-errors"\n'
                'include(":arrow-errors-core")\n'
                'project(":arrow-errors-core").projectDir = file("error-core")\n'
                'include(":arrow-errors-catalog")\n'
                'project(":arrow-errors-catalog").projectDir = file("error-catalog")\n'
                'include(":sample")\n',
                encoding="utf-8",
            )
            (root / "error-core").mkdir()
            (root / "error-catalog").mkdir()
            (root / "sample").mkdir()
            _, components = detect_gradle_repository(root)
            self.assertEqual(
                {
                    ("arrow-errors-core", Path("error-core")),
                    ("arrow-errors-catalog", Path("error-catalog")),
                    ("sample", Path("sample")),
                },
                set(components),
            )

    def test_multiline_include_list_is_detected(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "settings.gradle.kts").write_text(
                'rootProject.name = "sample"\n'
                "include(\n"
                '    ":core",\n'
                '    ":feature"\n'
                ")\n",
                encoding="utf-8",
            )
            for module in ("core", "feature"):
                (root / module).mkdir()
            _, components = detect_gradle_repository(root)
            self.assertEqual({("core", Path("core")), ("feature", Path("feature"))}, set(components))


class ComponentStructureTests(unittest.TestCase):
    def test_plain_jvm_layout_uses_source_and_tests(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            component = root / "auth"
            (component / "src/main/kotlin").mkdir(parents=True)
            (component / "src/test/kotlin").mkdir(parents=True)
            paths = component_structure(root, Path("auth"))
            self.assertEqual("src/main/kotlin", paths["source"])
            self.assertEqual("src/test/kotlin", paths["tests"])
            self.assertNotIn("source_commonMain", paths)

    def test_kmp_layout_detects_each_source_set(self):
        # Regression: Kotlin Multiplatform modules (found in two real repositories,
        # CatchMeIfYouCan and AMPM, during the Phase 10 pilot) never use src/main or
        # src/test at all -- every source set (commonMain, androidMain, commonTest,
        # ...) lives directly under src/. Before this, such a component's manifest
        # ended up with no source/tests paths at all, so task-context routing had no
        # search_roots to offer for it.
        with TemporaryDirectory() as name:
            root = Path(name)
            component = root / "feature/home"
            for source_set in ("commonMain", "androidMain", "commonTest", "androidUnitTest"):
                (component / "src" / source_set / "kotlin").mkdir(parents=True)
            (component / "src" / "commonMain" / "kotlin" / "Placeholder.txt").write_text("", encoding="utf-8")
            paths = component_structure(root, Path("feature/home"))
            self.assertEqual("src/commonMain/kotlin", paths["source_commonMain"])
            self.assertEqual("src/androidMain/kotlin", paths["source_androidMain"])
            self.assertEqual("src/commonTest/kotlin", paths["tests_commonTest"])
            self.assertEqual("src/androidUnitTest/kotlin", paths["tests_androidUnitTest"])
            self.assertNotIn("source", paths)
            self.assertNotIn("tests", paths)

    def test_kmp_source_set_without_kotlin_dir_is_ignored(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            component = root / "feature/home"
            (component / "src/commonMain/kotlin").mkdir(parents=True)
            (component / "src/commonMain/resources").mkdir(parents=True)
            paths = component_structure(root, Path("feature/home"))
            self.assertEqual("src/commonMain/kotlin", paths["source_commonMain"])
            self.assertEqual(1, sum(1 for key in paths if key.startswith("source_")))


class UnresolvedGradleIncludesTests(unittest.TestCase):
    def test_empty_when_everything_resolves(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "settings.gradle.kts").write_text(
                'rootProject.name = "sample"\ninclude(":core")\n', encoding="utf-8",
            )
            (root / "core").mkdir()
            self.assertEqual([], unresolved_gradle_includes(root))

    def test_empty_when_no_settings_file(self):
        with TemporaryDirectory() as name:
            self.assertEqual([], unresolved_gradle_includes(Path(name)))

    def test_flags_include_with_no_resolvable_directory(self):
        # e.g. a module included programmatically (looping over a directory
        # listing) rather than via a plain include(":name") with a fixed path --
        # no static parse, including projectDir remapping, can ever resolve this.
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "settings.gradle.kts").write_text(
                'rootProject.name = "sample"\ninclude(":ghost")\n', encoding="utf-8",
            )
            self.assertEqual([":ghost"], unresolved_gradle_includes(root))


class GradleProjectPathsTests(unittest.TestCase):
    def test_returns_the_real_project_path_for_a_remapped_module(self):
        # Regression: generate_gradle_manifests used to reconstruct build.project
        # from the component's directory (":" + relative.parts joined), which only
        # matches the real Gradle project path by convention -- projectDir
        # remapping breaks that convention on purpose, so the reconstruction
        # silently produced the wrong project path for any remapped module.
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "settings.gradle.kts").write_text(
                'rootProject.name = "arrow-errors"\n'
                'include(":arrow-errors-core")\n'
                'project(":arrow-errors-core").projectDir = file("error-core")\n',
                encoding="utf-8",
            )
            (root / "error-core").mkdir()
            self.assertEqual({"arrow-errors-core": ":arrow-errors-core"}, gradle_project_paths(root))

    def test_unremapped_module_project_path_matches_its_directory_convention(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "settings.gradle.kts").write_text(
                'rootProject.name = "sample"\ninclude(":core:api")\n', encoding="utf-8",
            )
            (root / "core/api").mkdir(parents=True)
            self.assertEqual({"core-api": ":core:api"}, gradle_project_paths(root))

    def test_empty_for_single_module_repository(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "settings.gradle.kts").write_text('rootProject.name = "solo"\n', encoding="utf-8")
            (root / "build.gradle.kts").write_text("", encoding="utf-8")
            self.assertEqual({}, gradle_project_paths(root))


if __name__ == "__main__":
    unittest.main()
