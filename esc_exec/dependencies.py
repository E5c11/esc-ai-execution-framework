from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from esc_exec.json_io import load_json, write_json
from esc_exec.manifests import REPOSITORY_MANIFEST
from esc_exec.model import ManifestState, ValidationResult
from esc_exec.yaml_io import load_yaml


DEPENDENCY_GRAPH = "esc-dependencies.json"
PROJECT_DEPENDENCY = re.compile(
    r"(?P<configuration>[A-Za-z][A-Za-z0-9]*)\s*\(\s*project\s*\(\s*[\"'](?P<project>:[^\"']+)[\"']\s*\)\s*\)"
)


def _digest(parts: list[bytes]) -> str:
    value = hashlib.sha256()
    for part in parts:
        value.update(len(part).to_bytes(8, "big"))
        value.update(part)
    return f"sha256:{value.hexdigest()}"


def build_dependency_graph(repository: Path) -> dict[str, Any]:
    repository = repository.resolve()
    repository_manifest_path = repository / REPOSITORY_MANIFEST
    repository_manifest = load_yaml(repository_manifest_path)
    nodes = []
    manifests: dict[str, tuple[Path, dict[str, Any]]] = {}
    project_to_component: dict[str, str] = {}
    inputs = [repository_manifest_path.read_bytes()]
    for declared in repository_manifest["components"]:
        manifest_path = repository / declared["manifest"]
        manifest = load_yaml(manifest_path)
        component_id = manifest["component"]["id"]
        project = manifest["build"]["project"]
        build_path = manifest_path.parent / manifest.get("paths", {}).get("build", "build.gradle.kts")
        manifests[component_id] = (manifest_path, manifest)
        project_to_component[project] = component_id
        inputs.append(manifest_path.read_bytes())
        if build_path.is_file():
            inputs.append(build_path.read_bytes())
        nodes.append({"id": component_id, "project": project, "manifest": declared["manifest"]})
    edges = []
    for consumer, (manifest_path, manifest) in manifests.items():
        build_path = manifest_path.parent / manifest.get("paths", {}).get("build", "build.gradle.kts")
        if not build_path.is_file():
            continue
        for match in PROJECT_DEPENDENCY.finditer(build_path.read_text(encoding="utf-8")):
            dependency = project_to_component.get(match.group("project"))
            if dependency and dependency != consumer:
                edges.append({
                    "consumer": consumer,
                    "dependency": dependency,
                    "configuration": match.group("configuration"),
                    "source": str(build_path.relative_to(repository)),
                })
    unique_edges = {(
        edge["consumer"], edge["dependency"], edge["configuration"], edge["source"]
    ): edge for edge in edges}
    return {
        "schema_version": 1,
        "generated_by": "esc-exec",
        "input_digest": _digest(inputs),
        "repository": repository_manifest["repository"]["id"],
        "nodes": sorted(nodes, key=lambda node: node["id"]),
        "edges": [unique_edges[key] for key in sorted(unique_edges)],
    }


def generate_dependency_graph(repository: Path) -> Path:
    output = repository.resolve() / DEPENDENCY_GRAPH
    write_json(output, build_dependency_graph(repository))
    return output


def validate_dependency_graph(repository: Path) -> ValidationResult:
    output = repository.resolve() / DEPENDENCY_GRAPH
    if not output.is_file():
        return ValidationResult(ManifestState.INCOMPLETE, str(output), ["Dependency graph is missing; run dependency generate."])
    try:
        expected = build_dependency_graph(repository)
        actual = load_json(output)
    except (KeyError, OSError, ValueError) as exc:
        return ValidationResult(ManifestState.INVALID, str(output), [str(exc)])
    if actual != expected:
        return ValidationResult(ManifestState.STALE, str(output), ["Gradle dependency inputs changed; regenerate the dependency graph."])
    return ValidationResult(ManifestState.VALID, str(output), [])


def analyze_impact(repository: Path, source_components: list[str], output: Path | None = None) -> dict[str, Any]:
    validation = validate_dependency_graph(repository)
    if validation.state != ManifestState.VALID:
        raise ValueError("; ".join(validation.messages))
    graph = load_json(repository.resolve() / DEPENDENCY_GRAPH)
    node_ids = {node["id"] for node in graph["nodes"]}
    missing = sorted(set(source_components) - node_ids)
    if missing:
        raise ValueError(f"components are not in dependency graph: {', '.join(missing)}")
    consumers: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for edge in graph["edges"]:
        consumers[edge["dependency"]].add(edge["consumer"])
    sources = sorted(set(source_components))
    direct = sorted({consumer for source in sources for consumer in consumers[source]} - set(sources))
    visited = set(sources)
    frontier = list(direct)
    transitive: set[str] = set()
    while frontier:
        current = frontier.pop(0)
        if current in visited:
            continue
        visited.add(current)
        transitive.add(current)
        frontier.extend(sorted(consumers[current] - visited))
    document = {
        "schema_version": 1,
        "repository": graph["repository"],
        "graph": DEPENDENCY_GRAPH,
        "source_components": sources,
        "direct_consumers": direct,
        "transitive_consumers": sorted(transitive),
        "affected_components": sorted(set(sources) | transitive),
    }
    if output:
        write_json(output, document)
    return document
