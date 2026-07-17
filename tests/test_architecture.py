from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.architecture import check_architecture, generate_architecture_profile
from esc_exec.contracts import validate_contract
from esc_exec.indexing import generate_indexes
from esc_exec.model import ManifestState
from esc_exec.yaml_io import load_yaml, write_yaml


class ArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        source = self.root / "content/src/main/kotlin"
        source.mkdir(parents=True)
        (source / "Allowed.kt").write_text("import com.example.core.Api\n", encoding="utf-8")
        (source / "Forbidden.kt").write_text(
            "package content\nimport com.example.portal.Secret\nimport com.example.portal.Other\n",
            encoding="utf-8",
        )
        write_yaml(self.root / "esc-execution.yaml", {
            "schema_version": 1,
            "repository": {"id": "repo", "type": "gradle-multi-project", "purpose": "test"},
            "components": [{"id": "content", "manifest": "content/esc-component.yaml"}],
        })
        write_yaml(self.root / "content/esc-component.yaml", {
            "schema_version": 1,
            "component": {"id": "content", "type": "gradle-module", "path": "content", "purpose": "content"},
            "build": {"system": "gradle", "project": ":content"},
            "paths": {"source": "src/main/kotlin"},
        })
        generate_indexes(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_reports_stable_bounded_rule_violations(self):
        profile_path = generate_architecture_profile(self.root, "content")
        profile = load_yaml(profile_path)
        profile["limits"] = {"max_violations_per_rule": 1, "max_evidence_chars": 30}
        profile["rules"] = [{
            "id": "content.no-portal-imports",
            "type": "forbidden-import",
            "description": "Content must not depend on portal.",
            "source_root": "src/main/kotlin",
            "patterns": ["com.example.portal.*"],
        }]
        write_yaml(profile_path, profile)
        generate_indexes(self.root)
        output = self.root / "architecture-report.json"
        report = check_architecture(self.root, "content", output)
        self.assertEqual("failed", report["status"])
        self.assertEqual("content.no-portal-imports", report["results"][0]["rule_id"])
        self.assertEqual(1, len(report["results"][0]["violations"]))
        self.assertEqual(1, report["results"][0]["violations_omitted"])
        self.assertEqual(ManifestState.VALID, validate_contract("architecture-report", output).state)

    def test_passes_required_and_forbidden_path_rules(self):
        profile_path = generate_architecture_profile(self.root, "content")
        profile = load_yaml(profile_path)
        profile["rules"] = [
            {"id": "content.source-required", "type": "required-path", "description": "Source exists.", "paths": ["src/main/kotlin"]},
            {"id": "content.no-legacy", "type": "forbidden-path", "description": "No legacy source.", "patterns": ["src/**/legacy/**"]},
        ]
        write_yaml(profile_path, profile)
        report = check_architecture(self.root, "content", self.root / "report.json")
        self.assertEqual("passed", report["status"])
