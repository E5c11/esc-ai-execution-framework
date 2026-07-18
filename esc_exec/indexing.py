from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from esc_exec.json_io import load_json, write_json
from esc_exec.manifests import (
    ESC_AI_DIR, repository_manifest_path, repository_manifest_relative_path,
)
from esc_exec.model import ManifestState, ValidationResult
from esc_exec.yaml_io import load_yaml


INDEX_FILE = "esc-index.json"
CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
NON_WORD = re.compile(r"[^a-z0-9]+")
STOP_WORDS = {
    "a", "an", "and", "change", "fix", "for", "in", "of", "on", "the", "to", "update",
}
ROLE_SUFFIXES = {
    "controllers": "Controller",
    "services": "Service",
    "configurations": "Config",
    "repositories": "Repository",
    "entities": "Entity",
}


def _tokens(value: str) -> set[str]:
    separated = CAMEL_BOUNDARY.sub(" ", value).lower()
    result: set[str] = set()
    for token in NON_WORD.split(separated):
        if not token or token in STOP_WORDS:
            continue
        result.add(token)
        if token.endswith("ies") and len(token) > 3:
            result.add(token[:-3] + "y")
        elif token.endswith("s") and not token.endswith("ss") and len(token) > 3:
            result.add(token[:-1])
    return result


def _digest(parts: list[bytes]) -> str:
    value = hashlib.sha256()
    for part in parts:
        value.update(len(part).to_bytes(8, "big"))
        value.update(part)
    return f"sha256:{value.hexdigest()}"


def _relative_kotlin_files(component_root: Path, source_path: str | None) -> list[Path]:
    if not source_path:
        return []
    source_root = component_root / source_path
    if not source_root.is_dir():
        return []
    return sorted(path.relative_to(component_root) for path in source_root.rglob("*.kt"))


def _package_areas(files: list[Path], source_path: str | None) -> list[str]:
    if not source_path:
        return []
    source_parts = Path(source_path).parts
    areas: set[str] = set()
    for path in files:
        package_parts = path.parent.parts[len(source_parts):]
        marker = next((i for i, part in enumerate(package_parts) if part == "backend"), None)
        if marker is not None:
            package_parts = package_parts[marker + 1:]
        if package_parts:
            areas.add("/".join(package_parts))
    return sorted(areas)


def _roles(files: list[Path]) -> tuple[dict[str, list[str]], dict[str, int]]:
    entry_points: dict[str, list[str]] = {role: [] for role in ("controllers", "services", "configurations")}
    counts = {role: 0 for role in ROLE_SUFFIXES}
    for path in files:
        stem = path.stem
        for role, suffix in ROLE_SUFFIXES.items():
            if stem.endswith(suffix):
                counts[role] += 1
                if role in entry_points:
                    entry_points[role].append(str(path))
                break
    return ({key: value for key, value in entry_points.items() if value}, counts)


def build_component_index(root: Path, repository_id: str, manifest_path: Path) -> dict[str, Any]:
    manifest = load_yaml(manifest_path)
    component = manifest["component"]
    component_root = root / component["path"]
    paths = manifest.get("paths", {})
    files = _relative_kotlin_files(component_root, paths.get("source"))
    entry_points, role_counts = _roles(files)
    ownership = component.get("ownership", {})
    routing = component.get("routing", {})
    aliases = sorted(set(routing.get("aliases", [])))
    entry_terms: set[str] = set()
    for role_paths in entry_points.values():
        for path in role_paths:
            entry_terms.update(_tokens(Path(path).stem))
    relative_manifest = manifest_path.relative_to(root)
    structural_parts = [manifest_path.read_bytes()]
    structural_parts.extend(str(path).encode("utf-8") for path in files)
    return {
        "schema_version": 1,
        "generated_by": "esc-exec",
        "input_digest": _digest(structural_parts),
        "repository": repository_id,
        "component": {
            "id": component["id"],
            "type": component["type"],
            "path": component["path"],
            "purpose": component["purpose"],
            "manifest": str(relative_manifest),
        },
        "routing": {
            "domains": sorted(set(ownership.get("domains", []))),
            "concerns": sorted(set(ownership.get("concerns", []))),
            "aliases": aliases,
            "entry_point_terms": sorted(entry_terms),
        },
        "search_roots": [
            str(Path(component["path"]) / value)
            for key, value in paths.items()
            if key in {"source", "tests", "resources", "test_resources", "migrations", "documentation"}
        ],
        "structure": {
            "package_areas": _package_areas(files, paths.get("source")),
            "entry_points": entry_points,
            "role_counts": role_counts,
        },
    }


def build_indexes(root: Path) -> tuple[dict[str, Any], list[tuple[Path, dict[str, Any]]]]:
    root = root.resolve()
    repository_path = repository_manifest_path(root)
    repository_manifest = load_yaml(repository_path)
    repository_id = repository_manifest["repository"]["id"]
    component_indexes: list[tuple[Path, dict[str, Any]]] = []
    root_components: list[dict[str, Any]] = []
    root_digest_parts = [repository_path.read_bytes()]
    for declared in repository_manifest["components"]:
        manifest_path = root / declared["manifest"]
        component_index = build_component_index(root, repository_id, manifest_path)
        index_path = manifest_path.parent / INDEX_FILE
        component_indexes.append((index_path, component_index))
        root_digest_parts.append(manifest_path.read_bytes())
        root_digest_parts.append(component_index["input_digest"].encode("utf-8"))
        routing = component_index["routing"]
        root_components.append({
            "id": component_index["component"]["id"],
            "purpose": component_index["component"]["purpose"],
            "path": component_index["component"]["path"],
            "manifest": declared["manifest"],
            "index": str(index_path.relative_to(root)),
            "routing": {
                "domains": routing["domains"],
                "concerns": routing["concerns"],
                "aliases": routing["aliases"],
            },
        })
    root_index = {
        "schema_version": 1,
        "generated_by": "esc-exec",
        "input_digest": _digest(root_digest_parts),
        "repository": {
            "id": repository_id,
            "purpose": repository_manifest["repository"].get("purpose", ""),
            "manifest": repository_manifest_relative_path(),
        },
        "components": root_components,
    }
    return root_index, component_indexes


def generate_indexes(root: Path) -> list[Path]:
    root = root.resolve()
    root_index, component_indexes = build_indexes(root)
    written: list[Path] = []
    for path, data in component_indexes:
        write_json(path, data)
        written.append(path)
    root_path = root / ESC_AI_DIR / INDEX_FILE
    write_json(root_path, root_index)
    return [root_path, *written]


def validate_indexes(root: Path) -> list[ValidationResult]:
    root = root.resolve()
    try:
        expected_root, expected_components = build_indexes(root)
    except (KeyError, OSError, ValueError) as exc:
        return [ValidationResult(ManifestState.INVALID, str(root / ESC_AI_DIR / INDEX_FILE), [str(exc)])]
    expected = [(root / ESC_AI_DIR / INDEX_FILE, expected_root), *expected_components]
    results: list[ValidationResult] = []
    for path, expected_data in expected:
        if not path.exists():
            results.append(ValidationResult(
                ManifestState.INCOMPLETE,
                str(path),
                [f"Index is missing. Run: esc-exec index generate {root}"],
            ))
            continue
        try:
            actual = load_json(path)
        except (OSError, ValueError) as exc:
            results.append(ValidationResult(ManifestState.INVALID, str(path), [str(exc)]))
            continue
        if actual != expected_data:
            results.append(ValidationResult(
                ManifestState.STALE,
                str(path),
                ["Index inputs or generated structure changed; regenerate and review the diff."],
            ))
        else:
            results.append(ValidationResult(ManifestState.VALID, str(path), []))
    return results


@dataclass(frozen=True)
class Match:
    component_id: str
    score: int
    reasons: tuple[str, ...]
    index: str
    search_roots: tuple[str, ...]


def match_components(root: Path, query: str) -> list[Match]:
    index = load_json(root.resolve() / ESC_AI_DIR / INDEX_FILE)
    query_tokens = _tokens(query)
    matches: list[Match] = []
    for component in index["components"]:
        routing = component["routing"]
        weighted = {
            "component": (_tokens(component["id"]), 8),
            "domain": (set().union(*(_tokens(value) for value in routing["domains"]), set()), 6),
            "alias": (set().union(*(_tokens(value) for value in routing["aliases"]), set()), 6),
            "concern": (set().union(*(_tokens(value) for value in routing["concerns"]), set()), 4),
            "purpose": (_tokens(component["purpose"]), 2),
        }
        score = 0
        reasons: list[str] = []
        for category, (terms, weight) in weighted.items():
            hits = sorted(query_tokens & terms)
            if hits:
                score += len(hits) * weight
                reasons.append(f"{category}: {', '.join(hits)}")
        if score:
            component_detail = load_json(root.resolve() / component["index"])
            matches.append(Match(
                component_id=component["id"],
                score=score,
                reasons=tuple(reasons),
                index=component["index"],
                search_roots=tuple(component_detail["search_roots"]),
            ))
    return sorted(matches, key=lambda match: (-match.score, match.component_id))
