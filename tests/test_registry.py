from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.model import ManifestState
from esc_exec.registry import RENAMED_FRAMEWORK_IDS, add_ecosystem, add_route, resolve_route, validate_registry


class RegistryTests(unittest.TestCase):
    def test_add_resolve_and_validate_route(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "config/repositories.yaml"
            repository = root / "repo"
            repository.mkdir()
            add_route(registry, "repositories", "sample", repository)
            self.assertEqual(repository.resolve(), resolve_route(registry, "repositories", "sample"))
            self.assertEqual(ManifestState.VALID, validate_registry(registry).state)

    def test_missing_target_is_stale(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "repositories.yaml"
            target = root / "repo"
            target.mkdir()
            add_route(registry, "repositories", "sample", target)
            target.rmdir()
            self.assertEqual(ManifestState.STALE, validate_registry(registry).state)

    def test_missing_route_requests_registration(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "repositories.yaml"
            with self.assertRaisesRegex(KeyError, "esc-exec route add repository sample"):
                resolve_route(registry, "repositories", "sample")

    def test_resolving_renamed_framework_reports_migration(self):
        old_id, new_id = next(iter(RENAMED_FRAMEWORK_IDS.items()))
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "repositories.yaml"
            framework = root / "framework"
            framework.mkdir()
            add_route(registry, "frameworks", new_id, framework)
            with self.assertRaisesRegex(KeyError, f"renamed to `{new_id}`"):
                resolve_route(registry, "frameworks", old_id)

    def test_registered_renamed_framework_id_is_stale(self):
        old_id = next(iter(RENAMED_FRAMEWORK_IDS))
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "repositories.yaml"
            framework = root / "framework"
            framework.mkdir()
            add_route(registry, "frameworks", old_id, framework)
            result = validate_registry(registry)
            self.assertEqual(ManifestState.STALE, result.state)
            self.assertTrue(any("renamed framework ID" in message for message in result.messages))

    def test_ecosystem_of_registered_repositories_is_valid(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "repositories.yaml"
            for repo_id in ("ampm-backend", "ampm-mobile"):
                repository = root / repo_id
                repository.mkdir()
                add_route(registry, "repositories", repo_id, repository)
            add_ecosystem(registry, "ampm", ["ampm-backend", "ampm-mobile"])
            self.assertEqual(ManifestState.VALID, validate_registry(registry).state)

    def test_ecosystem_referencing_unregistered_repository_is_invalid(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "repositories.yaml"
            repository = root / "ampm-backend"
            repository.mkdir()
            add_route(registry, "repositories", "ampm-backend", repository)
            add_ecosystem(registry, "ampm", ["ampm-backend", "ampm-mobile"])
            result = validate_registry(registry)
            self.assertEqual(ManifestState.INVALID, result.state)
            self.assertTrue(any("references unregistered repository: ampm-mobile" in message for message in result.messages))


if __name__ == "__main__":
    unittest.main()
