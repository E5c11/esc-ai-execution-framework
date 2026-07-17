from __future__ import annotations

from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path
import re
from typing import Any

from esc_exec.indexing import INDEX_FILE
from esc_exec.json_io import load_json, write_json
from esc_exec.yaml_io import load_yaml, write_yaml


RULE_ID = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _owned_path(root: Path, value: str, label: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{label} must be component-relative")
    return root / relative


def generate_architecture_profile(repository: Path, component_id: str) -> Path:
    index = load_json(repository / INDEX_FILE)
    component = next((item for item in index["components"] if item["id"] == component_id), None)
    if not component:
        raise ValueError(f"component is not in repository index: {component_id}")
    manifest_path = repository / component["manifest"]
    manifest = load_yaml(manifest_path)
    output = manifest_path.parent / "esc-architecture-profile.yaml"
    if output.exists():
        raise ValueError(f"architecture profile already exists: {output}")
    write_yaml(output, {
        "schema_version": 1,
        "profile": {"id": f"{component_id}-architecture", "component": component_id},
        "limits": {"max_violations_per_rule": 10, "max_evidence_chars": 200},
        "rules": [],
    })
    manifest.setdefault("paths", {})["architecture_profile"] = output.name
    write_yaml(manifest_path, manifest)
    return output


def _violations(component_root: Path, rule: dict[str, Any]) -> list[dict[str, Any]]:
    rule_type = rule["type"]
    violations: list[dict[str, Any]] = []
    if rule_type == "forbidden-import":
        source_root = _owned_path(component_root, rule.get("source_root", ""), "source_root")
        patterns = rule.get("patterns")
        if not source_root.is_dir() or not isinstance(patterns, list) or not patterns:
            raise ValueError(f"rule {rule['id']} requires an existing source_root and patterns")
        for path in sorted(source_root.rglob("*")):
            if path.suffix not in {".kt", ".java"} or not path.is_file():
                continue
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                stripped = line.strip()
                if not stripped.startswith("import "):
                    continue
                imported = stripped[7:].split(" as ", 1)[0].strip().rstrip(";")
                if any(fnmatchcase(imported, pattern) for pattern in patterns):
                    violations.append({"path": str(path.relative_to(component_root)), "line": line_number, "evidence": stripped})
    elif rule_type == "forbidden-path":
        patterns = rule.get("patterns")
        if not isinstance(patterns, list) or not patterns:
            raise ValueError(f"rule {rule['id']} requires patterns")
        if any(Path(pattern).is_absolute() or ".." in Path(pattern).parts for pattern in patterns):
            raise ValueError(f"rule {rule['id']} patterns must be component-relative")
        matched = {path for pattern in patterns for path in component_root.glob(pattern) if path.exists()}
        violations = [{"path": str(path.relative_to(component_root)), "evidence": "forbidden path exists"} for path in sorted(matched)]
    elif rule_type == "required-path":
        paths = rule.get("paths")
        if not isinstance(paths, list) or not paths:
            raise ValueError(f"rule {rule['id']} requires paths")
        for value in sorted(paths):
            if not _owned_path(component_root, value, "required path").exists():
                violations.append({"path": value, "evidence": "required path is missing"})
    else:
        raise ValueError(f"unsupported architecture rule type: {rule_type}")
    return violations


def check_architecture(repository: Path, component_id: str, output: Path) -> dict[str, Any]:
    index = load_json(repository / INDEX_FILE)
    component = next((item for item in index["components"] if item["id"] == component_id), None)
    if not component:
        raise ValueError(f"component is not in repository index: {component_id}")
    manifest_path = repository / component["manifest"]
    manifest = load_yaml(manifest_path)
    relative_profile = manifest.get("paths", {}).get("architecture_profile")
    if not relative_profile:
        raise ValueError(
            f"component {component_id} has no paths.architecture_profile; run: "
            f"esc-exec architecture profile generate {index['repository']['id']} {component_id}"
        )
    profile_path = _owned_path(manifest_path.parent, relative_profile, "architecture profile")
    profile = load_yaml(profile_path)
    rules = profile.get("rules")
    if profile.get("schema_version") != 1 or profile.get("profile", {}).get("component") != component_id:
        raise ValueError(f"invalid architecture profile for component {component_id}")
    if not isinstance(rules, list) or not rules:
        raise ValueError(f"architecture profile requires at least one authored rule: {profile_path}")
    rule_ids = [rule.get("id") for rule in rules if isinstance(rule, dict)]
    if len(rule_ids) != len(rules) or len(set(rule_ids)) != len(rule_ids) or any(not isinstance(rule_id, str) or not RULE_ID.fullmatch(rule_id) for rule_id in rule_ids):
        raise ValueError("architecture rule IDs must be present, unique, and stable lowercase dot/dash identifiers")
    limits = profile.get("limits", {})
    max_violations = limits.get("max_violations_per_rule")
    max_evidence = limits.get("max_evidence_chars")
    if not isinstance(max_violations, int) or not 1 <= max_violations <= 100 or not isinstance(max_evidence, int) or not 1 <= max_evidence <= 1000:
        raise ValueError("architecture profile limits are invalid")

    component_root = manifest_path.parent
    results = []
    for rule in sorted(rules, key=lambda item: item["id"]):
        all_violations = _violations(component_root, rule)
        included = []
        for violation in all_violations[:max_violations]:
            included.append({**violation, "evidence": violation["evidence"][:max_evidence]})
        results.append({
            "rule_id": rule["id"],
            "description": rule["description"],
            "status": "failed" if all_violations else "passed",
            "violations": included,
            "violations_omitted": len(all_violations) - len(included),
        })
    failed = sum(result["status"] == "failed" for result in results)
    total_violations = sum(len(result["violations"]) + result["violations_omitted"] for result in results)
    included = sum(len(result["violations"]) for result in results)
    document = {
        "schema_version": 1,
        "component": component_id,
        "profile": str(profile_path.relative_to(repository)),
        "status": "failed" if failed else "passed",
        "totals": {
            "rules": len(results), "passed": len(results) - failed, "failed": failed,
            "violations": total_violations, "violations_included": included,
            "violations_omitted": total_violations - included,
        },
        "results": results,
        "generated_at": _now(),
    }
    write_json(output, document)
    return document
