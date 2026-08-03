import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.contracts import validate_contract
from esc_exec.indexing import generate_indexes
from esc_exec.dependencies import generate_dependency_graph
from esc_exec.manifests import (
    component_manifest_path, component_manifest_relative_path, repository_manifest_path,
)
from esc_exec.model import ManifestState
from esc_exec.registry import add_route
from esc_exec.task_context import (
    build_task_context,
    build_verification_plan,
    generate_gradle_verification_profile,
)
from esc_exec.yaml_io import load_yaml, write_yaml


class TaskContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "content").mkdir()
        write_yaml(repository_manifest_path(self.root), {
            "schema_version": 1,
            "repository": {"id": "repo", "type": "gradle-multi-project", "purpose": "test"},
            "components": [{"id": "content", "manifest": component_manifest_relative_path("content")}],
        })
        write_yaml(component_manifest_path(self.root, "content"), {
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
        write_yaml(component_manifest_path(self.root, "content"), {
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
        write_yaml(component_manifest_path(self.root, "content"), {
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
        write_yaml(component_manifest_path(self.root, "content"), {
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

    def test_generated_verification_profile_declares_junit_report_locations(self):
        profile_path = generate_gradle_verification_profile(self.root, "content")
        profile = load_yaml(profile_path)
        report_profile_path = profile_path.parent / "esc-report-profile.yaml"
        self.assertTrue(report_profile_path.is_file())
        report_profile = load_yaml(report_profile_path)
        self.assertEqual(
            {"schema_version": 1, "profile": {"id": "content-report", "format": "junit-xml"},
             "limits": {"max_failures": 10, "max_message_chars": 500}},
            report_profile,
        )
        report_profile_relative = str(report_profile_path.relative_to(self.root))
        focused_report = profile["gates"]["focused"][0]["report"]
        component_report = profile["gates"]["component"][0]["report"]
        final_report = profile["gates"]["final"][0]["report"]
        self.assertEqual({"glob": "content/build/test-results/test/*.xml", "profile": report_profile_relative}, focused_report)
        self.assertEqual({"glob": "content/build/test-results/test/*.xml", "profile": report_profile_relative}, component_report)
        self.assertEqual({"glob": "**/build/test-results/test/*.xml", "profile": report_profile_relative}, final_report)
        manifest = load_yaml(component_manifest_path(self.root, "content"))
        self.assertEqual("esc-report-profile.yaml", manifest["paths"]["report_profile"])

    def test_verification_scope_defaults_to_repository_unchanged(self):
        generate_gradle_verification_profile(self.root, "content")
        generate_indexes(self.root)
        generate_dependency_graph(self.root)
        plan = build_verification_plan(self.root, self.task, self.root / "plan.json")
        final_checks = next(gate for gate in plan["gates"] if gate["id"] == "final")["checks"]
        self.assertEqual([["./gradlew", "test"]], [check["command"] for check in final_checks])

    def test_verification_scope_task_narrows_final_gate_to_scoped_components(self):
        generate_gradle_verification_profile(self.root, "content")
        generate_indexes(self.root)
        generate_dependency_graph(self.root)
        scoped_task = self.root / "task-scoped.yaml"
        task_document = load_yaml(self.task)
        task_document["scope"]["verification_scope"] = "task"
        write_yaml(scoped_task, task_document)
        plan = build_verification_plan(self.root, scoped_task, self.root / "plan.json")
        final_checks = next(gate for gate in plan["gates"] if gate["id"] == "final")["checks"]
        component_checks = next(gate for gate in plan["gates"] if gate["id"] == "component")["checks"]
        # Scoped down to content's own test task, not the repo-root `./gradlew test`
        # the generated profile's own `final` gate would otherwise declare.
        self.assertEqual([["./gradlew", ":content:test"]], [check["command"] for check in final_checks])
        self.assertEqual(component_checks, final_checks)
        self.assertEqual(ManifestState.VALID, validate_contract("verification-plan", self.root / "plan.json").state)

    def test_verification_scope_rejects_unknown_value(self):
        generate_gradle_verification_profile(self.root, "content")
        generate_indexes(self.root)
        generate_dependency_graph(self.root)
        bad_task = self.root / "task-bad-scope.yaml"
        task_document = load_yaml(self.task)
        task_document["scope"]["verification_scope"] = "everything"
        write_yaml(bad_task, task_document)
        with self.assertRaisesRegex(ValueError, "verification_scope"):
            build_verification_plan(self.root, bad_task, self.root / "plan.json")

    def test_coverage_check_omitted_when_not_declared(self):
        generate_gradle_verification_profile(self.root, "content")
        manifest_dir = component_manifest_path(self.root, "content").parent
        profile = load_yaml(manifest_dir / "esc-verification-profile.yaml")
        self.assertEqual(1, len(profile["gates"]["component"]))

    def test_coverage_check_emitted_for_kover(self):
        """plan/done/coverage-threshold-enforcement.md: real, verified-live map
        from a declared coverage tool to its Gradle report-generation task."""
        repository_path = repository_manifest_path(self.root)
        repository = load_yaml(repository_path)
        repository["testing"] = {"common": {"coverage": {"tool": "kover"}}}
        write_yaml(repository_path, repository)
        generate_gradle_verification_profile(self.root, "content")
        manifest_dir = component_manifest_path(self.root, "content").parent
        profile = load_yaml(manifest_dir / "esc-verification-profile.yaml")
        component_checks = profile["gates"]["component"]
        self.assertEqual(2, len(component_checks))
        coverage_check = component_checks[1]
        self.assertEqual("content-coverage", coverage_check["id"])
        self.assertEqual(["./gradlew", ":content:koverXmlReport"], coverage_check["command"])
        self.assertEqual("content/build/reports/kover/**/*.xml", coverage_check["report"]["glob"])
        coverage_report_profile = load_yaml(self.root / coverage_check["report"]["profile"])
        self.assertEqual("coverage-xml", coverage_report_profile["profile"]["format"])
        self.assertEqual("LINE", coverage_report_profile["limits"]["counter_type"])
        self.assertNotIn("threshold", coverage_report_profile["limits"])

    def test_coverage_check_emitted_for_jacoco_with_threshold(self):
        repository_path = repository_manifest_path(self.root)
        repository = load_yaml(repository_path)
        repository["testing"] = {"common": {"coverage": {"tool": "jacoco", "threshold": 80}}}
        write_yaml(repository_path, repository)
        generate_gradle_verification_profile(self.root, "content")
        manifest_dir = component_manifest_path(self.root, "content").parent
        profile = load_yaml(manifest_dir / "esc-verification-profile.yaml")
        coverage_check = profile["gates"]["component"][1]
        self.assertEqual(["./gradlew", ":content:jacocoTestReport"], coverage_check["command"])
        coverage_report_profile = load_yaml(self.root / coverage_check["report"]["profile"])
        self.assertEqual(80, coverage_report_profile["limits"]["threshold"])

    def test_unknown_coverage_tool_raises(self):
        repository_path = repository_manifest_path(self.root)
        repository = load_yaml(repository_path)
        repository["testing"] = {"common": {"coverage": {"tool": "codecov"}}}
        write_yaml(repository_path, repository)
        with self.assertRaisesRegex(ValueError, "coverage.tool"):
            generate_gradle_verification_profile(self.root, "content")

    def test_component_level_coverage_override_wins_over_repository(self):
        repository_path = repository_manifest_path(self.root)
        repository = load_yaml(repository_path)
        repository["testing"] = {"common": {"coverage": {"tool": "kover"}}}
        write_yaml(repository_path, repository)
        component_manifest = component_manifest_path(self.root, "content")
        data = load_yaml(component_manifest)
        data["testing"] = {"common": {"coverage": {"tool": "jacoco"}}}
        write_yaml(component_manifest, data)
        generate_gradle_verification_profile(self.root, "content")
        manifest_dir = component_manifest_path(self.root, "content").parent
        profile = load_yaml(manifest_dir / "esc-verification-profile.yaml")
        coverage_check = profile["gates"]["component"][1]
        self.assertEqual(["./gradlew", ":content:jacocoTestReport"], coverage_check["command"])

    def test_existing_report_profile_is_not_overwritten(self):
        manifest_path = component_manifest_path(self.root, "content")
        report_profile_path = manifest_path.parent / "esc-report-profile.yaml"
        write_yaml(report_profile_path, {
            "schema_version": 1,
            "profile": {"id": "hand-authored", "format": "junit-xml"},
            "limits": {"max_failures": 3, "max_message_chars": 50},
        })
        generate_gradle_verification_profile(self.root, "content")
        report_profile = load_yaml(report_profile_path)
        self.assertEqual("hand-authored", report_profile["profile"]["id"])
