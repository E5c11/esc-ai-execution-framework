from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.contracts import validate_contract
from esc_exec.dependencies import analyze_impact, generate_dependency_graph, validate_dependency_graph
from esc_exec.indexing import generate_indexes
from esc_exec.model import ManifestState
from esc_exec.task_context import build_verification_plan
from esc_exec.yaml_io import load_yaml
from esc_exec.yaml_io import write_yaml


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        components = [("a", []), ("b", ["a"]), ("c", ["b"]), ("d", ["a"])]
        write_yaml(self.root / "esc-execution.yaml", {
            "schema_version": 1,
            "repository": {"id": "repo", "type": "gradle-multi-project", "purpose": "test"},
            "components": [{"id": component, "manifest": f"{component}/esc-component.yaml"} for component, _ in components],
        })
        for component, dependencies in components:
            directory = self.root / component
            directory.mkdir()
            write_yaml(directory / "esc-component.yaml", {
                "schema_version": 1,
                "component": {"id": component, "type": "gradle-module", "path": component, "purpose": component},
                "build": {"system": "gradle", "project": f":{component}"},
                "paths": {"build": "build.gradle.kts"},
            })
            lines = [f'    implementation(project(":{dependency}"))' for dependency in dependencies]
            directory.joinpath("build.gradle.kts").write_text("dependencies {\n" + "\n".join(lines) + "\n}\n", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_generates_graph_and_transitive_consumer_impact(self):
        graph_path = generate_dependency_graph(self.root)
        self.assertEqual(ManifestState.VALID, validate_dependency_graph(self.root).state)
        self.assertEqual(ManifestState.VALID, validate_contract("dependency-graph", graph_path).state)
        output = self.root / "impact.json"
        impact = analyze_impact(self.root, ["a"], output)
        self.assertEqual(["b", "d"], impact["direct_consumers"])
        self.assertEqual(["b", "c", "d"], impact["transitive_consumers"])
        self.assertEqual(["a", "b", "c", "d"], impact["affected_components"])
        self.assertEqual(ManifestState.VALID, validate_contract("impact-analysis", output).state)

    def test_build_change_makes_graph_stale(self):
        generate_dependency_graph(self.root)
        with (self.root / "b/build.gradle.kts").open("a", encoding="utf-8") as output:
            output.write("// changed\n")
        self.assertEqual(ManifestState.STALE, validate_dependency_graph(self.root).state)

    def test_impact_consumers_populate_progressive_gate(self):
        for component in ("a", "b", "c", "d"):
            manifest_path = self.root / component / "esc-component.yaml"
            manifest = load_yaml(manifest_path)
            manifest["paths"]["verification_profile"] = "esc-verification-profile.yaml"
            write_yaml(manifest_path, manifest)
            write_yaml(self.root / component / "esc-verification-profile.yaml", {
                "schema_version": 1,
                "profile": {"id": f"{component}-verification", "component": component},
                "gates": {
                    "focused": [],
                    "component": [{"id": f"{component}-tests", "command": ["./gradlew", f":{component}:test"]}],
                    "impact": [],
                    "final": [{"id": "repository-tests", "command": ["./gradlew", "test"]}],
                },
            })
        generate_indexes(self.root)
        generate_dependency_graph(self.root)
        task = self.root / "task.yaml"
        write_yaml(task, {
            "schema_version": 1,
            "task": {"id": "task-a", "title": "Change A", "objective": "Change A", "repository": "repo", "status": "ready"},
            "scope": {"components": ["a"]},
            "completion_conditions": ["verified"],
        })
        plan = build_verification_plan(self.root, task, self.root / "plan.json")
        impact_gate = next(gate for gate in plan["gates"] if gate["id"] == "impact")
        self.assertEqual(["b", "c", "d"], plan["impact"]["consumer_components"])
        self.assertEqual(["b-tests", "c-tests", "d-tests"], [check["id"] for check in impact_gate["checks"]])
