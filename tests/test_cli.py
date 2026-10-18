from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.cli import main
from esc_exec.indexing import generate_indexes
from esc_exec.manifests import component_manifest_path, component_manifest_relative_path, repository_manifest_path
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


class ArchitectureTemplateCliTests(unittest.TestCase):
    def test_generates_a_web_app_profile_and_checks_it(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            for folder in ("src/features/feeds/domain", "src/app"):
                (root / folder).mkdir(parents=True)
            (root / "src/features/feeds/domain/rate.ts").write_text("export const rate = 1\n", encoding="utf-8")
            (root / "src/app/page.tsx").write_text("export default function Page() { return null }\n", encoding="utf-8")
            (root / "package.json").write_text('{"name": "web"}', encoding="utf-8")
            write_yaml(repository_manifest_path(root), {
                "schema_version": 1,
                "repository": {"id": "web", "type": "npm-package", "purpose": "test"},
                "components": [{"id": "web", "manifest": component_manifest_relative_path("web")}],
            })
            write_yaml(component_manifest_path(root, "web"), {
                "schema_version": 1,
                "component": {"id": "web", "type": "npm-package", "path": ".", "purpose": "web"},
                "build": {"system": "npm"},
                "paths": {"source": "src"},
            })
            generate_indexes(root)
            registry = root / "repositories.yaml"
            with redirect_stdout(StringIO()):
                main(["--registry", str(registry), "route", "add", "repository", "web", str(root)])
            output = StringIO()
            with redirect_stdout(output):
                generated = main([
                    "--registry", str(registry), "architecture", "profile", "generate", "web", "web",
                    "--template", "web-app",
                ])
            self.assertEqual(0, generated)
            self.assertNotIn("INCOMPLETE", output.getvalue())
            generate_indexes(root)
            with redirect_stdout(StringIO()):
                checked = main([
                    "--registry", str(registry), "architecture", "check", "web", "web", str(root / "report.json"),
                ])
            self.assertEqual(0, checked)


if __name__ == "__main__":
    unittest.main()
