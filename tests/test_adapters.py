from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import json

from esc_exec.adapters import GradleAdapter, NpmAdapter, component_id_from_identifier, detect_build_system


class AdaptersTests(unittest.TestCase):
    def test_detects_gradle_repository(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "settings.gradle.kts").write_text(
                'rootProject.name = "sample"\ninclude(":content")\n', encoding="utf-8",
            )
            (root / "content").mkdir()
            repository_id, components, adapter = detect_build_system(root)
            self.assertEqual("sample", repository_id)
            self.assertEqual([("content", Path("content"))], components)
            self.assertIsInstance(adapter, GradleAdapter)
            self.assertEqual("gradle", adapter.name)

    def test_detects_npm_repository(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "package.json").write_text(json.dumps({"name": "garage-triage"}), encoding="utf-8")
            repository_id, components, adapter = detect_build_system(root)
            self.assertEqual("garage-triage", repository_id)
            self.assertEqual([("garage-triage", Path("."))], components)
            self.assertIsInstance(adapter, NpmAdapter)
            self.assertEqual("npm", adapter.name)

    def test_raises_when_no_adapter_detects_a_build(self):
        with TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "No supported build-system adapter"):
                detect_build_system(Path(temp))


class UnresolvedTests(unittest.TestCase):
    def test_gradle_adapter_reports_unresolved_includes(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "settings.gradle.kts").write_text(
                'rootProject.name = "sample"\ninclude(":ghost")\n', encoding="utf-8",
            )
            self.assertEqual([":ghost"], GradleAdapter().unresolved(root))

    def test_gradle_adapter_reports_nothing_unresolved_when_everything_resolves(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "settings.gradle.kts").write_text(
                'rootProject.name = "sample"\ninclude(":content")\n', encoding="utf-8",
            )
            (root / "content").mkdir()
            self.assertEqual([], GradleAdapter().unresolved(root))

    def test_npm_adapter_never_reports_anything_unresolved(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "package.json").write_text(json.dumps({"name": "sample"}), encoding="utf-8")
            self.assertEqual([], NpmAdapter().unresolved(root))


class ComponentIdFromIdentifierTests(unittest.TestCase):
    def test_strips_leading_colon_and_collapses_separators(self):
        self.assertEqual("core-api", component_id_from_identifier(":core:api"))

    def test_matches_what_tier_1_derives_for_the_same_gradle_path(self):
        # detect_gradle_repository derives component IDs the same way for a
        # resolved include(...) path -- this must match exactly, or an
        # AI-resolved identifier would get a different ID than Tier 1 would have
        # assigned had it resolved the same path deterministically.
        self.assertEqual(":core:api".lstrip(":").replace(":", "-"), component_id_from_identifier(":core:api"))

    def test_identifier_without_leading_colon_is_unaffected(self):
        self.assertEqual("app", component_id_from_identifier("app"))


if __name__ == "__main__":
    unittest.main()
