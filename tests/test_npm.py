from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from esc_exec.npm import (
    detect_npm_frameworks_and_targets, detect_npm_repository, npm_component_structure,
)


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


class WorkspaceDetectionTests(unittest.TestCase):
    def _write_package(self, path: Path, name: str | None = None) -> None:
        path.mkdir(parents=True, exist_ok=True)
        (path / "package.json").write_text(json.dumps({"name": name} if name else {}), encoding="utf-8")

    def test_array_workspaces_field_detects_each_member(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "package.json").write_text(
                json.dumps({"name": "monorepo", "workspaces": ["packages/*"]}), encoding="utf-8",
            )
            self._write_package(root / "packages/app", "app")
            self._write_package(root / "packages/lib", "lib")
            repository_id, components = detect_npm_repository(root)
            self.assertEqual("monorepo", repository_id)
            self.assertEqual(
                {("app", Path("packages/app")), ("lib", Path("packages/lib"))}, set(components),
            )

    def test_object_shaped_workspaces_field_is_supported(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "package.json").write_text(
                json.dumps({"name": "monorepo", "workspaces": {"packages": ["packages/*"]}}), encoding="utf-8",
            )
            self._write_package(root / "packages/app", "app")
            repository_id, components = detect_npm_repository(root)
            self.assertEqual([("app", Path("packages/app"))], components)

    def test_pnpm_workspace_yaml_is_supported(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "package.json").write_text(json.dumps({"name": "monorepo"}), encoding="utf-8")
            (root / "pnpm-workspace.yaml").write_text("packages:\n  - 'packages/*'\n", encoding="utf-8")
            self._write_package(root / "packages/app", "app")
            repository_id, components = detect_npm_repository(root)
            self.assertEqual([("app", Path("packages/app"))], components)

    def test_negated_pattern_excludes_a_match(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "package.json").write_text(
                json.dumps({"name": "monorepo", "workspaces": ["packages/*", "!packages/excluded"]}),
                encoding="utf-8",
            )
            self._write_package(root / "packages/app", "app")
            self._write_package(root / "packages/excluded", "excluded")
            _, components = detect_npm_repository(root)
            self.assertEqual([("app", Path("packages/app"))], components)

    def test_member_without_name_field_falls_back_to_directory_name(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "package.json").write_text(
                json.dumps({"name": "monorepo", "workspaces": ["packages/*"]}), encoding="utf-8",
            )
            self._write_package(root / "packages/nameless")
            _, components = detect_npm_repository(root)
            self.assertEqual([("nameless", Path("packages/nameless"))], components)

    def test_node_modules_is_never_treated_as_a_workspace_member(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "package.json").write_text(
                json.dumps({"name": "monorepo", "workspaces": ["packages/**"]}), encoding="utf-8",
            )
            self._write_package(root / "packages/app", "app")
            self._write_package(root / "packages/app/node_modules/some-dependency", "some-dependency")
            _, components = detect_npm_repository(root)
            self.assertEqual([("app", Path("packages/app"))], components)

    def test_workspaces_field_present_but_matching_nothing_falls_back_to_single_component(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "package.json").write_text(
                json.dumps({"name": "solo", "workspaces": ["packages/*"]}), encoding="utf-8",
            )
            repository_id, components = detect_npm_repository(root)
            self.assertEqual("solo", repository_id)
            self.assertEqual([("solo", Path("."))], components)

    def test_no_workspaces_field_stays_single_component(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            (root / "package.json").write_text(json.dumps({"name": "solo"}), encoding="utf-8")
            (root / "packages/app").mkdir(parents=True)
            (root / "packages/app/package.json").write_text(json.dumps({"name": "app"}), encoding="utf-8")
            repository_id, components = detect_npm_repository(root)
            self.assertEqual([("solo", Path("."))], components)


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


class NpmFrameworkDetectionTests(unittest.TestCase):
    def test_missing_package_json_detects_nothing(self):
        with TemporaryDirectory() as name:
            frameworks, targets = detect_npm_frameworks_and_targets(Path(name) / "package.json")
            self.assertEqual({}, frameworks)
            self.assertEqual([], targets)

    def test_malformed_json_detects_nothing(self):
        with TemporaryDirectory() as name:
            package_json = Path(name) / "package.json"
            package_json.write_text("{not valid json", encoding="utf-8")
            frameworks, targets = detect_npm_frameworks_and_targets(package_json)
            self.assertEqual({}, frameworks)
            self.assertEqual([], targets)

    def test_detects_known_dependency(self):
        with TemporaryDirectory() as name:
            package_json = Path(name) / "package.json"
            package_json.write_text(
                json.dumps({"dependencies": {"next": "14.0.0"}}), encoding="utf-8",
            )
            frameworks, targets = detect_npm_frameworks_and_targets(package_json)
            self.assertEqual({"ui": "next"}, frameworks)
            self.assertEqual([], targets)

    def test_unrecognized_dependency_is_not_reported(self):
        with TemporaryDirectory() as name:
            package_json = Path(name) / "package.json"
            package_json.write_text(
                json.dumps({"dependencies": {"totally-unmapped-lib": "1.0.0"}}), encoding="utf-8",
            )
            frameworks, _ = detect_npm_frameworks_and_targets(package_json)
            self.assertEqual({}, frameworks)

    def test_dev_dependency_is_also_detected(self):
        with TemporaryDirectory() as name:
            package_json = Path(name) / "package.json"
            package_json.write_text(
                json.dumps({"devDependencies": {"react-hook-form": "7.0.0"}}), encoding="utf-8",
            )
            frameworks, _ = detect_npm_frameworks_and_targets(package_json)
            self.assertEqual({"forms": "react-hook-form"}, frameworks)

    def test_both_known_frameworks_detected_at_once(self):
        with TemporaryDirectory() as name:
            package_json = Path(name) / "package.json"
            package_json.write_text(
                json.dumps({
                    "dependencies": {"next": "14.0.0"},
                    "devDependencies": {"react-hook-form": "7.0.0"},
                }),
                encoding="utf-8",
            )
            frameworks, targets = detect_npm_frameworks_and_targets(package_json)
            self.assertEqual({"ui": "next", "forms": "react-hook-form"}, frameworks)
            self.assertEqual([], targets)

    def test_targets_is_always_empty(self):
        with TemporaryDirectory() as name:
            package_json = Path(name) / "package.json"
            package_json.write_text(
                json.dumps({"dependencies": {"next": "14.0.0"}}), encoding="utf-8",
            )
            _, targets = detect_npm_frameworks_and_targets(package_json)
            self.assertEqual([], targets)


if __name__ == "__main__":
    unittest.main()
