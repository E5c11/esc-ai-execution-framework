import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.contracts import validate_contract
from esc_exec.indexing import generate_indexes
from esc_exec.dependencies import generate_dependency_graph
from esc_exec.model import ManifestState
from esc_exec.registry import add_route
from esc_exec.task_context import (
    build_task_context,
    build_verification_plan,
    generate_gradle_verification_profile,
)
from esc_exec.yaml_io import write_yaml


class TaskContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "content").mkdir()
        write_yaml(self.root / "esc-execution.yaml", {
            "schema_version": 1,
            "repository": {"id": "repo", "type": "gradle-multi-project", "purpose": "test"},
            "components": [{"id": "content", "manifest": "content/esc-component.yaml"}],
        })
        write_yaml(self.root / "content/esc-component.yaml", {
            "schema_version": 1,
            "component": {"id": "content", "type": "gradle-module", "path": "content", "purpose": "Owns content"},
            "build": {"system": "gradle", "project": ":content"},
            "paths": {"source": "src/main", "tests": "src/test"},
        })
        generate_indexes(self.root)
        generate_dependency_graph(self.root)
        self.task = self.root / "task.yaml"
        write_yaml(self.task, {
            "schema_version": 1,
            "task": {"id": "task-1", "title": "Change content", "objective": "Update content", "repository": "repo", "status": "ready"},
            "scope": {"components": ["content"], "paths": ["content/src/main/A.kt"]},
            "completion_conditions": ["tests pass"],
            "references": ["content/esc-index.json"],
        })

    def tearDown(self):
        self.temporary.cleanup()

    def _register_architecture_framework(self, documents: list[dict]) -> Path:
        registry = self.root / "registry.yaml"
        framework_root = self.root / "architecture-framework"
        framework_root.mkdir()
        (framework_root / "index.json").write_text(
            json.dumps({"generated": "2026-01-01T00:00:00Z", "count": len(documents), "documents": documents}),
            encoding="utf-8",
        )
        add_route(registry, "frameworks", "esc-ai-architecture-framework", framework_root)
        return registry

    def test_resolves_declared_architecture_documents(self):
        write_yaml(self.root / "content/esc-component.yaml", {
            "schema_version": 1,
            "component": {"id": "content", "type": "gradle-module", "path": "content", "purpose": "Owns content"},
            "build": {"system": "gradle", "project": ":content"},
            "paths": {"source": "src/main", "tests": "src/test"},
            "architecture": {"profile_ids": ["ORCH-BE-FEAT"]},
        })
        generate_indexes(self.root)
        generate_dependency_graph(self.root)
        registry = self._register_architecture_framework([
            {"id": "CORE-DI", "path": "core/dependency-inversion.md", "layer": "core", "requires": []},
            {"id": "ORCH-BE-FEAT", "path": "feature-orchestrators/backend/feature.md",
             "layer": "feature-orchestrators", "requires": ["CORE-DI"]},
        ])
        output = self.root / "task-context.json"
        context = build_task_context(self.root, self.task, output, registry_path=registry)
        architecture = context["routing"]["components"][0]["architecture"]
        self.assertEqual(["ORCH-BE-FEAT"], architecture["profile_ids"])
        self.assertEqual(["CORE-DI", "ORCH-BE-FEAT"], [doc["id"] for doc in architecture["documents"]])
        self.assertNotIn("missing", architecture)
        self.assertNotIn("stubs", architecture)
        self.assertEqual(ManifestState.VALID, validate_contract("task-context", output).state)

    def test_missing_registry_path_with_declared_selector_raises(self):
        write_yaml(self.root / "content/esc-component.yaml", {
            "schema_version": 1,
            "component": {"id": "content", "type": "gradle-module", "path": "content", "purpose": "Owns content"},
            "build": {"system": "gradle", "project": ":content"},
            "paths": {"source": "src/main", "tests": "src/test"},
            "architecture": {"profile_ids": ["ORCH-BE-FEAT"]},
        })
        generate_indexes(self.root)
        generate_dependency_graph(self.root)
        with self.assertRaisesRegex(ValueError, "no registry_path was provided"):
            build_task_context(self.root, self.task, self.root / "task-context.json")

    def test_missing_and_stub_architecture_documents_are_reported(self):
        write_yaml(self.root / "content/esc-component.yaml", {
            "schema_version": 1,
            "component": {"id": "content", "type": "gradle-module", "path": "content", "purpose": "Owns content"},
            "build": {"system": "gradle", "project": ":content"},
            "paths": {"source": "src/main", "tests": "src/test"},
            "architecture": {"profile_ids": ["ORCH-BE-FEAT", "ARCH-NOT-REAL"]},
        })
        generate_indexes(self.root)
        generate_dependency_graph(self.root)
        registry = self._register_architecture_framework([
            {"id": "ORCH-BE-FEAT", "path": "feature-orchestrators/backend/feature.md",
             "layer": "feature-orchestrators", "requires": [], "status": "stub"},
        ])
        output = self.root / "task-context.json"
        context = build_task_context(self.root, self.task, output, registry_path=registry)
        architecture = context["routing"]["components"][0]["architecture"]
        self.assertEqual(["ARCH-NOT-REAL"], architecture["missing"])
        self.assertEqual(["ORCH-BE-FEAT"], architecture["stubs"])
        self.assertEqual(ManifestState.VALID, validate_contract("task-context", output).state)

    def test_builds_bounded_context_from_declared_components(self):
        output = self.root / "task-context.json"
        context = build_task_context(self.root, self.task, output)
        self.assertEqual(["content"], [item["id"] for item in context["routing"]["components"]])
        self.assertEqual(["content/src/main", "content/src/test"], context["routing"]["components"][0]["search_roots"])
        self.assertEqual(ManifestState.VALID, validate_contract("task-context", output).state)

    def test_context_rejects_scope_beyond_explicit_bound(self):
        with self.assertRaisesRegex(ValueError, "exceeds context bounds"):
            build_task_context(self.root, self.task, self.root / "context.json", max_paths=0)

    def test_generates_profile_and_progressive_plan(self):
        with self.assertRaisesRegex(ValueError, "verification profile"):
            build_verification_plan(self.root, self.task, self.root / "plan.json")
        profile = generate_gradle_verification_profile(self.root, "content")
        self.assertTrue(profile.is_file())
        generate_indexes(self.root)
        generate_dependency_graph(self.root)
        plan_path = self.root / "verification-plan.json"
        plan = build_verification_plan(self.root, self.task, plan_path)
        self.assertEqual(
            ["input-required", "ready", "not-applicable", "ready"],
            [gate["status"] for gate in plan["gates"]],
        )
        self.assertEqual(ManifestState.VALID, validate_contract("verification-plan", plan_path).state)
