from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from esc_exec.npm import (
    detect_npm_frameworks_and_targets, detect_npm_repository, detect_package_manager, npm_component_structure,
    npm_verification_gates,
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


class NpmVerificationGatesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def _package(self, scripts: dict, path: str = ".", **extra) -> None:
        directory = self.root / path
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "package.json").write_text(json.dumps({"name": "x", "scripts": scripts, **extra}), encoding="utf-8")

    def test_builds_progressive_gates_from_package_scripts(self):
        self._package({"typecheck": "tsc --noEmit", "lint": "eslint .", "test": "vitest run", "build": "next build"})
        gates, notes = npm_verification_gates(self.root, "web", ".")
        self.assertEqual(["npm", "run", "test", "--", "{test_filter}"], gates["focused"][0]["command"])
        self.assertEqual(["test_filter"], gates["focused"][0]["requires"])
        self.assertEqual(
            [["npm", "run", "typecheck"], ["npm", "run", "lint"], ["npm", "run", "test"]],
            [check["command"] for check in gates["component"]],
        )
        self.assertEqual([["npm", "run", "build"]], [check["command"] for check in gates["final"]])
        self.assertEqual([], gates["impact"])
        self.assertEqual([], notes)

    def test_a_missing_test_script_raises_instead_of_generating_an_empty_gate(self):
        self._package({"build": "next build"})
        with self.assertRaisesRegex(ValueError, "no `test` script"):
            npm_verification_gates(self.root, "web", ".")

    def test_a_bare_vitest_script_gets_run_so_it_does_not_watch(self):
        self._package({"test": "vitest"})
        gates, _ = npm_verification_gates(self.root, "web", ".")
        self.assertEqual(["npm", "run", "test", "--", "run"], gates["component"][-1]["command"])
        self.assertEqual(["npm", "run", "test", "--", "run", "{test_filter}"], gates["focused"][0]["command"])

    def test_missing_lint_typecheck_and_build_are_recorded_not_hidden(self):
        self._package({"test": "jest"})
        (self.root / "tsconfig.json").write_text("{}", encoding="utf-8")
        _, notes = npm_verification_gates(self.root, "web", ".")
        self.assertTrue(any("lint" in note for note in notes))
        self.assertTrue(any("typecheck" in note for note in notes))
        self.assertTrue(any("build" in note for note in notes))

    def test_falls_back_to_tsc_when_typescript_is_installed_and_no_script_exists(self):
        self._package({"test": "jest"}, devDependencies={"typescript": "5.6.0"})
        (self.root / "tsconfig.json").write_text("{}", encoding="utf-8")
        gates, _ = npm_verification_gates(self.root, "web", ".")
        self.assertEqual(["npx", "--no-install", "tsc", "--noEmit"], gates["component"][0]["command"])

    def test_package_manager_follows_the_lockfile(self):
        self._package({"test": "vitest run"})
        (self.root / "pnpm-lock.yaml").write_text("", encoding="utf-8")
        self.assertEqual("pnpm", detect_package_manager(self.root, self.root))
        gates, _ = npm_verification_gates(self.root, "web", ".")
        self.assertEqual(["pnpm", "run", "test"], gates["component"][-1]["command"])
        self.assertEqual(["pnpm", "run", "test", "{test_filter}"], gates["focused"][0]["command"])

    def test_workspace_member_runs_in_its_own_directory_and_adds_the_repository_test(self):
        self._package({"test": "vitest run"})
        self._package({"test": "vitest run", "build": "next build"}, path="apps/web")
        gates, _ = npm_verification_gates(self.root, "web", "apps/web")
        self.assertEqual(["npm", "--prefix", "apps/web", "run", "test"], gates["component"][-1]["command"])
        self.assertEqual(
            [["npm", "run", "test"], ["npm", "--prefix", "apps/web", "run", "build"]],
            [check["command"] for check in gates["final"]],
        )


if __name__ == "__main__":
    unittest.main()
