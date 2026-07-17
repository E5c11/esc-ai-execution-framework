from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.model import ManifestState
from esc_exec.registry import add_route, resolve_route, validate_registry


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


if __name__ == "__main__":
    unittest.main()
