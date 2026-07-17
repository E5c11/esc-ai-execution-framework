from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.manifests import generate_gradle_manifests, validate_repository
from esc_exec.model import ManifestState
from esc_exec.yaml_io import load_yaml, write_yaml


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "settings.gradle.kts").write_text(
            'rootProject.name = "sample"\ninclude(":core:api")\ninclude(":feature")\n',
            encoding="utf-8",
        )
        for component in (self.root / "core/api", self.root / "feature"):
            (component / "src/main/kotlin").mkdir(parents=True)
            (component / "build.gradle.kts").write_text("", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_generation_discovers_nested_gradle_components(self):
        generated = generate_gradle_manifests(self.root)
        self.assertEqual(3, len(generated))
        repository = load_yaml(self.root / "esc-execution.yaml")
        self.assertEqual("sample", repository["repository"]["id"])
        self.assertEqual(
            ["core/api/esc-component.yaml", "feature/esc-component.yaml"],
            [item["manifest"] for item in repository["components"]],
        )

    def test_generated_component_is_incomplete_until_purpose_is_authored(self):
        generate_gradle_manifests(self.root)
        results = validate_repository(self.root)
        self.assertTrue(any(result.state == ManifestState.INCOMPLETE for result in results))

    def test_regeneration_preserves_human_purpose(self):
        generate_gradle_manifests(self.root)
        manifest_path = self.root / "feature/esc-component.yaml"
        manifest = load_yaml(manifest_path)
        manifest["component"]["purpose"] = "Owns the sample feature."
        write_yaml(manifest_path, manifest)
        generate_gradle_manifests(self.root)
        self.assertEqual("Owns the sample feature.", load_yaml(manifest_path)["component"]["purpose"])

    def test_detects_undeclared_component_as_stale(self):
        generate_gradle_manifests(self.root)
        repository_path = self.root / "esc-execution.yaml"
        repository = load_yaml(repository_path)
        repository["components"] = repository["components"][:1]
        write_yaml(repository_path, repository)
        results = validate_repository(self.root)
        self.assertEqual(ManifestState.STALE, results[0].state)

    def test_schema_documents_are_valid_yaml_mappings(self):
        schemas = Path(__file__).parents[1] / "schemas"
        for schema in schemas.glob("*.schema.yaml"):
            self.assertIn("$schema", load_yaml(schema))


if __name__ == "__main__":
    unittest.main()
