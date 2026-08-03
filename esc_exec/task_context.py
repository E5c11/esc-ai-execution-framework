from __future__ import annotations

from pathlib import Path
from typing import Any

from esc_exec.architecture_lookup import load_architecture_index, resolve_architecture_docs, stub_documents
from esc_exec.indexing import INDEX_FILE, validate_indexes
from esc_exec.json_io import load_json, write_json
from esc_exec.manifests import ESC_AI_DIR, merged_testing, repository_manifest_path, resolve_testing_fact
from esc_exec.registry import resolve_route
from esc_exec.yaml_io import load_yaml, write_yaml
from esc_exec.model import ManifestState
from esc_exec.dependencies import analyze_impact


GATES = ("focused", "component", "impact", "final")
ARCHITECTURE_FRAMEWORK_ID = "esc-ai-architecture-framework"

# Small, explicit map from a declared testing.coverage.tool to the real Gradle task
# that generates its JaCoCo-format-compatible XML report -- see
# plan/done/coverage-threshold-enforcement.md. An unknown tool name is a real error
# at profile-generation time, not a silently-skipped coverage check.
COVERAGE_GRADLE_TASKS = {"kover": "koverXmlReport", "jacoco": "jacocoTestReport"}


def _profile_ids(manifest: dict[str, Any]) -> list[str]:
    return manifest.get("architecture", {}).get("profile_ids", []) or []


def build_task_context(
    repository: Path,
    task_path: Path,
    output: Path,
    max_components: int = 10,
    max_paths: int = 30,
    max_references: int = 30,
    registry_path: Path | None = None,
) -> dict[str, Any]:
    task_document = load_yaml(task_path)
    task = task_document["task"]
    scope = task_document["scope"]
    components = scope["components"]
    paths = scope.get("paths", [])
    references = task_document.get("references", [])
    if len(components) > max_components or len(paths) > max_paths or len(references) > max_references:
        raise ValueError("task scope exceeds context bounds; split the task or raise explicit limits")
    index_results = validate_indexes(repository)
    invalid_indexes = [result for result in index_results if result.state != ManifestState.VALID]
    if invalid_indexes:
        raise ValueError("repository indexes are missing, stale, or invalid; regenerate and validate them")
    root_index = load_json(repository / ESC_AI_DIR / INDEX_FILE)
    if root_index["repository"]["id"] != task["repository"]:
        raise ValueError("task repository does not match repository index")
    indexed = {item["id"]: item for item in root_index["components"]}
    missing = sorted(set(components) - set(indexed))
    if missing:
        raise ValueError(f"task components are not in the repository index: {', '.join(missing)}")
    repository_profile_ids = _profile_ids(load_yaml(repository_manifest_path(repository)))
    architecture_index: dict[str, dict[str, Any]] | None = None
    selected = []
    for component_id in components:
        item = indexed[component_id]
        detail = load_json(repository / item["index"])
        component_manifest = load_yaml(repository / item["manifest"])
        profile_ids = list(dict.fromkeys(repository_profile_ids + _profile_ids(component_manifest)))
        entry = {
            "id": item["id"],
            "purpose": item["purpose"],
            "manifest": item["manifest"],
            "index": item["index"],
            "search_roots": detail["search_roots"],
        }
        if profile_ids:
            if registry_path is None:
                raise ValueError(
                    f"component {component_id} declares architecture.profile_ids but no "
                    "registry_path was provided to resolve the architecture framework"
                )
            if architecture_index is None:
                framework_root = resolve_route(registry_path, "frameworks", ARCHITECTURE_FRAMEWORK_ID)
                architecture_index = load_architecture_index(framework_root)
            documents, missing_docs = resolve_architecture_docs(profile_ids, architecture_index)
            stubs = stub_documents(documents)
            entry["architecture"] = {
                "profile_ids": profile_ids,
                "documents": [
                    {"id": document["id"], "path": document["path"], "layer": document.get("layer", "")}
                    for document in documents
                ],
            }
            if missing_docs:
                entry["architecture"]["missing"] = missing_docs
            if stubs:
                entry["architecture"]["stubs"] = [document["id"] for document in stubs]
        selected.append(entry)
    document = {
        "schema_version": 1,
        "task": {"id": task["id"], "repository": task["repository"], "objective": task["objective"]},
        "routing": {
            "repository_index": f"{ESC_AI_DIR}/{INDEX_FILE}",
            "input_digest": root_index.get("input_digest", ""),
            "components": selected,
        },
        "scope": {
            "paths": paths,
            "references": references,
            "completion_conditions": task_document["completion_conditions"],
        },
        "bounds": {
            "max_components": max_components,
            "max_paths": max_paths,
            "max_references": max_references,
        },
    }
    if not document["routing"]["input_digest"]:
        document["routing"].pop("input_digest")
    write_json(output, document)
    return document


def generate_gradle_verification_profile(repository: Path, component_id: str) -> Path:
    root_index = load_json(repository / ESC_AI_DIR / INDEX_FILE)
    component = next((item for item in root_index["components"] if item["id"] == component_id), None)
    if not component:
        raise ValueError(f"component is not in repository index: {component_id}")
    manifest_path = repository / component["manifest"]
    manifest = load_yaml(manifest_path)
    if manifest["build"]["system"] != "gradle":
        raise ValueError("automatic verification-profile generation currently supports Gradle only")
    profile_path = manifest_path.parent / "esc-verification-profile.yaml"
    if profile_path.exists():
        raise ValueError(f"verification profile already exists: {profile_path}")
    project = manifest["build"]["project"]
    test_task = f"{project}:test" if project != ":" else "test"
    component_path = manifest["component"]["path"]

    report_profile_path = manifest_path.parent / "esc-report-profile.yaml"
    if not report_profile_path.exists():
        write_yaml(report_profile_path, {
            "schema_version": 1,
            "profile": {"id": f"{component_id}-report", "format": "junit-xml"},
            "limits": {"max_failures": 10, "max_message_chars": 500},
        })
    report_profile_relative = str(report_profile_path.relative_to(repository))

    def _report(glob: str) -> dict[str, str]:
        return {"glob": glob, "profile": report_profile_relative}

    component_glob = f"{component_path}/build/test-results/test/*.xml"
    repository_glob = "**/build/test-results/test/*.xml"
    profile = {
        "schema_version": 1,
        "profile": {"id": f"{component_id}-verification", "component": component_id},
        "gates": {
            "focused": [{
                "id": f"{component_id}-focused-tests",
                "command": ["./gradlew", test_task, "--tests", "{test_filter}"],
                "requires": ["test_filter"],
                "report": _report(component_glob),
            }],
            "component": [{
                "id": f"{component_id}-tests",
                "command": ["./gradlew", test_task],
                "report": _report(component_glob),
            }],
            "impact": [],
            "final": [{
                "id": "repository-tests",
                "command": ["./gradlew", "test"],
                "report": _report(repository_glob),
            }],
        },
    }

    repository_manifest = load_yaml(repository_manifest_path(repository))
    coverage = resolve_testing_fact(merged_testing(repository_manifest, manifest), "coverage")
    if coverage:
        tool = coverage.get("tool")
        if tool not in COVERAGE_GRADLE_TASKS:
            raise ValueError(
                f"testing.coverage.tool must be one of {sorted(COVERAGE_GRADLE_TASKS)} for automatic "
                f"Gradle coverage-check generation, got {tool!r}"
            )
        coverage_task = COVERAGE_GRADLE_TASKS[tool]
        coverage_gradle_task = f"{project}:{coverage_task}" if project != ":" else coverage_task
        coverage_report_profile_path = manifest_path.parent / "esc-coverage-report-profile.yaml"
        if not coverage_report_profile_path.exists():
            limits: dict[str, Any] = {"counter_type": "LINE"}
            if coverage.get("threshold") is not None:
                limits["threshold"] = coverage["threshold"]
            write_yaml(coverage_report_profile_path, {
                "schema_version": 1,
                "profile": {"id": f"{component_id}-coverage-report", "format": "coverage-xml"},
                "limits": limits,
            })
        coverage_report_profile_relative = str(coverage_report_profile_path.relative_to(repository))
        # Ordered after the test check (see plan/done/coverage-threshold-
        # enforcement.md open question 3): coverage without passing tests first
        # is meaningless data, and stop_on_failure already means this check
        # never even runs if the test check above it failed.
        profile["gates"]["component"].append({
            "id": f"{component_id}-coverage",
            "command": ["./gradlew", coverage_gradle_task],
            "report": {
                "glob": f"{component_path}/build/reports/{tool}/**/*.xml",
                "profile": coverage_report_profile_relative,
            },
        })

    write_yaml(profile_path, profile)
    paths = manifest.setdefault("paths", {})
    paths["verification_profile"] = profile_path.name
    paths["report_profile"] = report_profile_path.name
    write_yaml(manifest_path, manifest)
    return profile_path


def build_verification_plan(repository: Path, task_path: Path, output: Path) -> dict[str, Any]:
    task_document = load_yaml(task_path)
    task = task_document["task"]
    verification_scope = task_document["scope"].get("verification_scope", "repository")
    if verification_scope not in {"repository", "task"}:
        raise ValueError(f"scope.verification_scope must be 'repository' or 'task', got {verification_scope!r}")
    root_index = load_json(repository / ESC_AI_DIR / INDEX_FILE)
    indexed = {item["id"]: item for item in root_index["components"]}
    profiles = []
    gate_checks: dict[str, list[dict[str, Any]]] = {gate: [] for gate in GATES}
    seen: dict[str, set[tuple[str, ...]]] = {gate: set() for gate in GATES}
    for component_id in task_document["scope"]["components"]:
        if component_id not in indexed:
            raise ValueError(f"task component is not in repository index: {component_id}")
        manifest_path = repository / indexed[component_id]["manifest"]
        manifest = load_yaml(manifest_path)
        relative_profile = manifest.get("paths", {}).get("verification_profile")
        if not relative_profile:
            raise ValueError(
                f"component {component_id} has no paths.verification_profile; run: "
                f"esc-exec verification profile generate {task['repository']} {component_id}"
            )
        relative_profile_path = Path(relative_profile)
        if relative_profile_path.is_absolute() or ".." in relative_profile_path.parts:
            raise ValueError(f"verification profile must be owned by component {component_id}")
        profile_path = manifest_path.parent / relative_profile_path
        if not profile_path.is_file():
            raise ValueError(f"declared verification profile is missing: {profile_path}")
        profile = load_yaml(profile_path)
        if profile.get("schema_version") != 1 or profile.get("profile", {}).get("component") != component_id:
            raise ValueError(f"invalid verification profile for component {component_id}")
        profiles.append(str(profile_path.relative_to(repository)))
        for gate in GATES:
            checks = profile.get("gates", {}).get(gate)
            if not isinstance(checks, list):
                raise ValueError(f"verification profile {profile_path} must define gate {gate}")
            for check in checks:
                command = tuple(check.get("command", []))
                if not check.get("id") or not command:
                    raise ValueError(f"verification profile {profile_path} contains an invalid {gate} check")
                if command not in seen[gate]:
                    seen[gate].add(command)
                    gate_checks[gate].append(check)
    impact = analyze_impact(repository, task_document["scope"]["components"])
    for component_id in impact["transitive_consumers"]:
        manifest_path = repository / indexed[component_id]["manifest"]
        manifest = load_yaml(manifest_path)
        relative_profile = manifest.get("paths", {}).get("verification_profile")
        if not relative_profile:
            raise ValueError(
                f"impacted component {component_id} has no paths.verification_profile; run: "
                f"esc-exec verification profile generate {task['repository']} {component_id}"
            )
        relative_profile_path = Path(relative_profile)
        if relative_profile_path.is_absolute() or ".." in relative_profile_path.parts:
            raise ValueError(f"verification profile must be owned by component {component_id}")
        profile_path = manifest_path.parent / relative_profile_path
        profile = load_yaml(profile_path)
        if profile.get("schema_version") != 1 or profile.get("profile", {}).get("component") != component_id:
            raise ValueError(f"invalid verification profile for impacted component {component_id}")
        relative_profile_value = str(profile_path.relative_to(repository))
        if relative_profile_value not in profiles:
            profiles.append(relative_profile_value)
        checks = profile.get("gates", {}).get("component")
        if not isinstance(checks, list):
            raise ValueError(f"verification profile {profile_path} must define gate component")
        for check in checks:
            command = tuple(check.get("command", []))
            if not check.get("id") or not command:
                raise ValueError(f"verification profile {profile_path} contains an invalid component check")
            if command not in seen["impact"]:
                seen["impact"].add(command)
                gate_checks["impact"].append(check)
    if verification_scope == "task":
        # plan/active/pre-flight-doctor-and-gate-prerequisites.md design 5: skip
        # the generated profile's repo-root `final` gate (which pulls in every
        # component's tests, including ones this task never touches) and instead
        # aggregate just this task's own scoped components' and their impacted
        # consumers' own test tasks -- exactly the checks `component`/`impact`
        # already collected above, deduped the same way those two were built.
        final_checks: list[dict[str, Any]] = []
        final_seen: set[tuple[str, ...]] = set()
        for check in gate_checks["component"] + gate_checks["impact"]:
            command = tuple(check.get("command", []))
            if command not in final_seen:
                final_seen.add(command)
                final_checks.append(check)
        gate_checks["final"] = final_checks
    gates = []
    for gate in GATES:
        checks = gate_checks[gate]
        status = "not-applicable" if not checks else "input-required" if any(check.get("requires") for check in checks) else "ready"
        gates.append({"id": gate, "status": status, "checks": checks})
    document = {
        "schema_version": 1,
        "task_id": task["id"],
        "profiles": profiles,
        "strategy": {"order": list(GATES), "stop_on_failure": True},
        "impact": {
            "graph": impact["graph"],
            "source_components": impact["source_components"],
            "consumer_components": impact["transitive_consumers"],
        },
        "gates": gates,
    }
    write_json(output, document)
    return document
