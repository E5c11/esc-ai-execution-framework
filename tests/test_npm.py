from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from esc_exec.npm import detect_npm_repository, npm_component_structure


class DetectNpmRepositoryTests(unittest.TestCase):
    def test_reads_repository_id_from_package_json_name(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "package.json").write_text(json.dumps({"name": "garage-triage"}), encoding="utf-8")
            repository_id, components = detect_npm_repository(root)
            self.assertEqual("garage-triage", repository_id)
            self.assertEqual([("garage-triage", Path("."))], components)

    def test_falls_back_to_directory_name_when_name_field_missing(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "package.json").write_text(json.dumps({}), encoding="utf-8")
            repository_id, components = detect_npm_repository(root)
            self.assertEqual(root.name, repository_id)
            self.assertEqual([(root.name, Path("."))], components)

    def test_raises_when_no_package_json(self):
        with TemporaryDirectory() as name:
            with self.assertRaisesRegex(ValueError, "No package.json"):
                detect_npm_repository(Path(name))


class NpmComponentStructureTests(unittest.TestCase):
    def test_src_layout_uses_source_and_tests(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "src").mkdir()
            (root / "__tests__").mkdir()
            (root / "package.json").write_text("{}", encoding="utf-8")
            paths = npm_component_structure(root, Path("."))
            self.assertEqual("src", paths["source"])
            self.assertEqual("__tests__", paths["tests"])
            self.assertEqual("package.json", paths["build"])
            self.assertNotIn("source_app", paths)
            self.assertNotIn("source_pages", paths)

    def test_app_router_without_src_uses_source_app_prefix(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "app").mkdir()
            paths = npm_component_structure(root, Path("."))
            self.assertEqual("app", paths["source_app"])
            self.assertNotIn("source", paths)

    def test_pages_router_without_src_uses_source_pages_prefix(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "pages").mkdir()
            paths = npm_component_structure(root, Path("."))
            self.assertEqual("pages", paths["source_pages"])
            self.assertNotIn("source", paths)


if __name__ == "__main__":
    unittest.main()
