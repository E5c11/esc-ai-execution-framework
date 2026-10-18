from __future__ import annotations

from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path
import re
from typing import Any

from esc_exec.indexing import INDEX_FILE
from esc_exec.architecture_templates import profile_from_template
from esc_exec.json_io import load_json, write_json
from esc_exec.manifests import ESC_AI_DIR
from esc_exec.ts_imports import (
    EXCLUDED_DIRECTORIES, TS_SUFFIXES, glob_matches, is_test_file, load_tsconfig_paths,
    module_specifiers, resolve_import,
)
from esc_exec.yaml_io import load_yaml, write_yaml


RULE_ID = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _owned_path(root: Path, value: str, label: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{label} must be component-relative")
    return root / relative


def generate_architecture_profile(repository: Path, component_id: str, template: str | None = None) -> Path:
    """Write ``esc-architecture-profile.yaml`` for a component.

    Without ``template`` the profile has no rules: dependency direction, legacy boundaries and required
    conventions cannot be inferred safely, so the author writes them. With a ``template`` the profile
    starts from that template's rules (see ``esc_exec.architecture_templates``).
    """
    index = load_json(repository / ESC_AI_DIR / INDEX_FILE)
    component = next((item for item in index["components"] if item["id"] == component_id), None)
    if not component:
        raise ValueError(f"component is not in repository index: {component_id}")
    manifest_path = repository / component["manifest"]
    manifest = load_yaml(manifest_path)
    output = manifest_path.parent / "esc-architecture-profile.yaml"
    # Onboarding writes an empty profile for every component. A template may fill that one in; a profile
    # that already has rules is the author's and is never overwritten.
    refilling_empty = output.exists() and template is not None and not (load_yaml(output) or {}).get("rules")
    if output.exists() and not refilling_empty:
        raise ValueError(f"architecture profile already exists: {output}")
    document: dict[str, Any] = {
        "schema_version": 1,
        "profile": {"id": f"{component_id}-architecture", "component": component_id},
        "limits": {"max_violations_per_rule": 10, "max_evidence_chars": 200},
        "rules": [],
    }
    if template is not None:
        document.update(profile_from_template(template, repository / component["path"]))
    write_yaml(output, document)
    manifest.setdefault("paths", {})["architecture_profile"] = output.name
    write_yaml(manifest_path, manifest)
    return output


_LEGACY_SUFFIXES = {".kt", ".java"}
_VACUOUS = "rule scanned no source files, so it cannot have checked anything"


def _expand_patterns(rule: dict[str, Any], pattern_sets: dict[str, Any]) -> dict[str, Any]:
    """Replace each ``{name}`` entry in a rule's patterns with the profile's pattern set of that name."""
    patterns = rule.get("patterns")
    if not isinstance(patterns, list):
        return rule
    expanded: list[str] = []
    for pattern in patterns:
        if isinstance(pattern, str) and pattern.startswith("{") and pattern.endswith("}"):
            values = pattern_sets.get(pattern[1:-1])
            if not isinstance(values, list) or not values:
                raise ValueError(f"rule {rule['id']} uses an unknown or empty pattern set: {pattern}")
            expanded.extend(values)
        else:
            expanded.append(pattern)
    return {**rule, "patterns": expanded}


def _source_directories(component_root: Path, rule: dict[str, Any]) -> list[tuple[Path, Path]]:
    """``(directory, unit)`` pairs. ``unit`` is the directory a root glob matched, which is the
    "own feature" for ``except_own_feature``."""
    roots = rule.get("source_roots")
    if roots is None:
        roots = [rule.get("source_root", "")]
    if not isinstance(roots, list) or not roots:
        raise ValueError(f"rule {rule['id']} requires an existing source_root and patterns")
    directories: list[tuple[Path, Path]] = []
    for value in roots:
        pattern = _owned_path(component_root, value, "source_root")
        matched = [pattern] if value == "" else sorted(component_root.glob(value))
        directories.extend((path, path) for path in matched if path.is_dir())
    if not directories and not rule.get("optional", False):
        raise ValueError(f"rule {rule['id']} requires an existing source_root and patterns")
    return directories


def _excluded(relative: Path, rule: dict[str, Any], suffix: str) -> bool:
    if suffix in _LEGACY_SUFFIXES:
        return False
    if any(part in EXCLUDED_DIRECTORIES for part in relative.parts):
        return True
    if not rule.get("include_tests", False) and is_test_file(relative):
        return True
    return any(glob_matches(pattern, relative.as_posix()) for pattern in rule.get("exclude", []))


def _forbidden_imports(component_root: Path, rule: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    patterns = rule.get("patterns")
    if not isinstance(patterns, list) or not patterns:
        raise ValueError(f"rule {rule['id']} requires an existing source_root and patterns")
    directories = _source_directories(component_root, rule)
    if not directories:
        # An optional layer folder that does not exist yet has nothing to violate. A rule that is not
        # optional never gets here: it already failed above.
        return [], 0
    ts_paths = load_tsconfig_paths(component_root)
    own_feature = bool(rule.get("except_own_feature", False))
    violations: list[dict[str, Any]] = []
    scanned: set[Path] = set()
    for directory, unit in directories:
        unit_relative = unit.relative_to(component_root).as_posix()
        for path in sorted(directory.rglob("*")):
            if path in scanned or not path.is_file() or path.suffix not in _LEGACY_SUFFIXES | TS_SUFFIXES:
                continue
            relative = path.relative_to(component_root)
            if _excluded(relative, rule, path.suffix):
                continue
            scanned.add(path)
            text = path.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            if path.suffix in _LEGACY_SUFFIXES:
                for line_number, line in enumerate(lines, start=1):
                    stripped = line.strip()
                    if not stripped.startswith("import "):
                        continue
                    imported = stripped[7:].split(" as ", 1)[0].strip().rstrip(";")
                    if any(fnmatchcase(imported, pattern) for pattern in patterns):
                        violations.append({"path": str(relative), "line": line_number, "evidence": stripped})
                continue
            for line_number, specifier in module_specifiers(text):
                resolved = resolve_import(specifier, path, component_root, ts_paths)
                candidates = [specifier] if resolved is None else [specifier, resolved]
                if not any(glob_matches(pattern, candidate) for pattern in patterns for candidate in candidates):
                    continue
                if own_feature and resolved is not None and (resolved == unit_relative or resolved.startswith(unit_relative + "/")):
                    continue
                violations.append({"path": str(relative), "line": line_number, "evidence": lines[line_number - 1].strip()})
    if not scanned:
        violations.append({"path": ", ".join(str(value) for value in (rule.get("source_roots") or [rule.get("source_root", "")])), "evidence": _VACUOUS})
    return violations, len(scanned)


def _violations(component_root: Path, rule: dict[str, Any]) -> tuple[list[dict[str, Any]], int | None]:
    """Violations for one rule, and how many source files it scanned (``None`` for rules that scan none)."""
    rule_type = rule["type"]
    violations: list[dict[str, Any]] = []
    if rule_type == "forbidden-import":
        return _forbidden_imports(component_root, rule)
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
    return violations, None


def check_architecture(repository: Path, component_id: str, output: Path) -> dict[str, Any]:
    index = load_json(repository / ESC_AI_DIR / INDEX_FILE)
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

    # Rules check the component's real source tree, which is repository_root /
    # component["path"] -- completely independent of where the manifest bundle
    # itself is stored (manifest_path.parent, now .esc-ai/components/<id>/).
    component_root = repository / component["path"]
    pattern_sets = profile.get("pattern_sets") or {}
    results = []
    for rule in sorted(rules, key=lambda item: item["id"]):
        all_violations, files_scanned = _violations(component_root, _expand_patterns(rule, pattern_sets))
        included = []
        for violation in all_violations[:max_violations]:
            included.append({**violation, "evidence": violation["evidence"][:max_evidence]})
        result = {
            "rule_id": rule["id"],
            "description": rule["description"],
            "status": "failed" if all_violations else "passed",
            "violations": included,
            "violations_omitted": len(all_violations) - len(included),
        }
        if files_scanned is not None:
            result["files_scanned"] = files_scanned
        results.append(result)
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
