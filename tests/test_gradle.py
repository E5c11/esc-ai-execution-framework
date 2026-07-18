from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.gradle import component_structure, detect_gradle_repository


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


if __name__ == "__main__":
    unittest.main()
