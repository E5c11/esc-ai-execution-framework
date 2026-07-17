from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.cli import main
from esc_exec.yaml_io import load_yaml, write_yaml


class CliTests(unittest.TestCase):
    def test_repository_route_uses_repositories_key(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "repositories.yaml"
            repository = root / "repo"
            repository.mkdir()
            with redirect_stdout(StringIO()):
                exit_code = main([
                    "--registry", str(registry),
                    "route", "add", "repository", "sample", str(repository),
                ])
            self.assertEqual(0, exit_code)
            data = load_yaml(registry)
            self.assertIn("sample", data["repositories"])
            self.assertNotIn("repositorys", data)

    def test_registry_validation_rejects_unknown_top_level_fields(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "repositories.yaml"
            write_yaml(registry, {
                "schema_version": 1,
                "repositories": {},
                "frameworks": {},
                "repositorys": {},
            })
            with redirect_stdout(StringIO()):
                exit_code = main(["--registry", str(registry), "route", "validate"])
            self.assertEqual(1, exit_code)


if __name__ == "__main__":
    unittest.main()
