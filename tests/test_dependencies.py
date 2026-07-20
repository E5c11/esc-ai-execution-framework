from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.contracts import validate_contract
from esc_exec.dependencies import (
    _project_path_to_accessor, analyze_impact, detect_gradle_frameworks_and_targets,
    generate_dependency_graph, validate_dependency_graph,
)
from esc_exec.indexing import generate_indexes
from esc_exec.json_io import load_json
from esc_exec.manifests import (
    component_manifest_path, component_manifest_relative_path, repository_manifest_path,
)
from esc_exec.model import ManifestState
from esc_exec.task_context import build_verification_plan
from esc_exec.yaml_io import load_yaml
from esc_exec.yaml_io import write_yaml


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        components = [("a", []), ("b", ["a"]), ("c", ["b"]), ("d", ["a"])]
        write_yaml(repository_manifest_path(self.root), {
            "schema_version": 1,
            "repository": {"id": "repo", "type": "gradle-multi-project", "purpose": "test"},
            "components": [
                {"id": component, "manifest": component_manifest_relative_path(component)}
                for component, _ in components
            ],
        })
        for component, dependencies in components:
            # The component's real source directory (root/<component>/) is
            # deliberately distinct from where its manifest bundle is stored
            # (.esc-ai/components/<component>/) -- this is what makes this fixture a
            # real regression test for the build_path resolution fix in
            # esc_exec.dependencies: if build_path were still resolved relative to
            # manifest_path.parent, it would never find build.gradle.kts here.
            directory = self.root / component
            directory.mkdir()
            write_yaml(component_manifest_path(self.root, component), {
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
            manifest_path = component_manifest_path(self.root, component)
            manifest = load_yaml(manifest_path)
            manifest["paths"]["verification_profile"] = "esc-verification-profile.yaml"
            write_yaml(manifest_path, manifest)
            write_yaml(manifest_path.parent / "esc-verification-profile.yaml", {
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


class NonGradleDependencyGraphTests(unittest.TestCase):
    def test_npm_component_is_an_edgeless_node_not_a_crash(self):
        # Regression: build_dependency_graph used to hard-require
        # manifest["build"]["project"], a Gradle-only concept -- an npm component
        # (no "project" key at all) crashed apply_onboarding_answers's dependency
        # graph step with a bare KeyError.
        with TemporaryDirectory() as name:
            root = Path(name)
            write_yaml(repository_manifest_path(root), {
                "schema_version": 1,
                "repository": {"id": "repo", "type": "npm-package", "purpose": "test"},
                "components": [{"id": "app", "manifest": component_manifest_relative_path("app")}],
            })
            (root / "app").mkdir()
            write_yaml(component_manifest_path(root, "app"), {
                "schema_version": 1,
                "component": {"id": "app", "type": "npm-package", "path": "app", "purpose": "app"},
                "build": {"system": "npm"},
                "paths": {"build": "package.json"},
            })
            (root / "app/package.json").write_text("{}", encoding="utf-8")
            graph_path = generate_dependency_graph(root)
            self.assertEqual(ManifestState.VALID, validate_dependency_graph(root).state)
            graph = load_json(graph_path)
            self.assertEqual([{"id": "app", "project": None, "manifest": component_manifest_relative_path("app")}], graph["nodes"])
            self.assertEqual([], graph["edges"])


class TypesafeProjectAccessorTests(unittest.TestCase):
    def test_accessor_conversion_matches_gradle(self):
        # Verified against a real project (CatchMeIfYouCan) built with
        # enableFeaturePreview("TYPESAFE_PROJECT_ACCESSORS"): every one of
        # these path -> accessor pairs was observed directly in its build
        # scripts (e.g. `implementation(projects.appHost)`,
        # `implementation(projects.core.designsystem)`).
        self.assertEqual("appHost", _project_path_to_accessor(":app-host"))
        self.assertEqual("androidApp", _project_path_to_accessor(":androidApp"))
        self.assertEqual("core.common", _project_path_to_accessor(":core:common"))
        self.assertEqual("core.designsystem", _project_path_to_accessor(":core:designsystem"))
        self.assertEqual("feature.home", _project_path_to_accessor(":feature:home"))

    def test_generates_graph_from_typesafe_accessors(self):
        # Regression test for the parser gap found while onboarding
        # CatchMeIfYouCan: repositories built exclusively with type-safe
        # project accessors (`projects.foo.bar`) previously produced a
        # dependency graph with zero edges, since PROJECT_DEPENDENCY only
        # matched the string-literal `project(":foo:bar")` form.
        with TemporaryDirectory() as name:
            root = Path(name)
            components = [("core-common", []), ("app-host", ["core-common"])]
            write_yaml(repository_manifest_path(root), {
                "schema_version": 1,
                "repository": {"id": "repo", "type": "gradle-multi-project", "purpose": "test"},
                "components": [
                    {"id": component, "manifest": component_manifest_relative_path(component)}
                    for component, _ in components
                ],
            })
            projects = {"core-common": ":core:common", "app-host": ":app-host"}
            for component, dependencies in components:
                directory = root / component
                directory.mkdir()
                write_yaml(component_manifest_path(root, component), {
                    "schema_version": 1,
                    "component": {"id": component, "type": "gradle-module", "path": component, "purpose": component},
                    "build": {"system": "gradle", "project": projects[component]},
                    "paths": {"build": "build.gradle.kts"},
                })
                accessor_lines = [
                    f'    implementation(projects.{_project_path_to_accessor(projects[dependency])})'
                    for dependency in dependencies
                ]
                directory.joinpath("build.gradle.kts").write_text(
                    "dependencies {\n" + "\n".join(accessor_lines) + "\n}\n", encoding="utf-8",
                )
            graph_path = generate_dependency_graph(root)
            self.assertEqual(ManifestState.VALID, validate_dependency_graph(root).state)
            self.assertEqual(ManifestState.VALID, validate_contract("dependency-graph", graph_path).state)
            output = root / "impact.json"
            impact = analyze_impact(root, ["core-common"], output)
            self.assertEqual(["app-host"], impact["direct_consumers"])


class GradleFrameworkDetectionTests(unittest.TestCase):
    def test_missing_build_file_detects_nothing(self):
        with TemporaryDirectory() as temp:
            frameworks, targets = detect_gradle_frameworks_and_targets(Path(temp) / "build.gradle.kts")
            self.assertEqual({}, frameworks)
            self.assertEqual([], targets)

    def test_detects_known_network_and_database_frameworks(self):
        with TemporaryDirectory() as temp:
            build_path = Path(temp) / "build.gradle.kts"
            build_path.write_text(
                'dependencies {\n'
                '    implementation("io.ktor:ktor-client-core:2.3.0")\n'
                '    implementation("androidx.room:room-runtime:2.6.1")\n'
                '}\n',
                encoding="utf-8",
            )
            frameworks, targets = detect_gradle_frameworks_and_targets(build_path)
            self.assertEqual({"network": "ktor", "database": "room"}, frameworks)
            self.assertEqual([], targets)

    def test_unrecognized_coordinate_is_not_reported(self):
        with TemporaryDirectory() as temp:
            build_path = Path(temp) / "build.gradle.kts"
            build_path.write_text(
                'dependencies {\n    implementation("com.example.totally:unmapped-lib:1.0")\n}\n',
                encoding="utf-8",
            )
            frameworks, _ = detect_gradle_frameworks_and_targets(build_path)
            self.assertEqual({}, frameworks)

    def test_test_scoped_dependency_is_excluded(self):
        with TemporaryDirectory() as temp:
            build_path = Path(temp) / "build.gradle.kts"
            build_path.write_text(
                'dependencies {\n    testImplementation("io.ktor:ktor-client-mock:2.3.0")\n}\n',
                encoding="utf-8",
            )
            frameworks, _ = detect_gradle_frameworks_and_targets(build_path)
            self.assertEqual({}, frameworks)

    def test_project_dependency_is_never_mistaken_for_external(self):
        with TemporaryDirectory() as temp:
            build_path = Path(temp) / "build.gradle.kts"
            build_path.write_text(
                'dependencies {\n    implementation(project(":core:common"))\n    implementation(projects.core.common)\n}\n',
                encoding="utf-8",
            )
            frameworks, _ = detect_gradle_frameworks_and_targets(build_path)
            self.assertEqual({}, frameworks)

    def test_detects_kmp_ios_target(self):
        with TemporaryDirectory() as temp:
            build_path = Path(temp) / "build.gradle.kts"
            build_path.write_text(
                'kotlin {\n    android()\n    ios()\n    jvm()\n}\n',
                encoding="utf-8",
            )
            _, targets = detect_gradle_frameworks_and_targets(build_path)
            self.assertEqual(["ios"], targets)

    def test_no_ios_target_detects_no_targets(self):
        with TemporaryDirectory() as temp:
            build_path = Path(temp) / "build.gradle.kts"
            build_path.write_text('kotlin {\n    android()\n    jvm()\n}\n', encoding="utf-8")
            _, targets = detect_gradle_frameworks_and_targets(build_path)
            self.assertEqual([], targets)
