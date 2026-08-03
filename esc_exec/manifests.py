from __future__ import annotations

from pathlib import Path
from typing import Any

from esc_exec.framework_descriptor import check_framework_compatibility
from esc_exec.adapters import detect_build_system
from esc_exec.gradle import component_structure, detect_gradle_repository, gradle_project_paths
from esc_exec.model import ManifestState, ValidationResult
from esc_exec.npm import detect_npm_repository, npm_component_structure
from esc_exec.registry import RENAMED_FRAMEWORK_IDS
from esc_exec.yaml_io import load_yaml, write_yaml


REPOSITORY_MANIFEST = "esc-execution.yaml"
COMPONENT_MANIFEST = "esc-component.yaml"

# build.system values a generated component manifest is allowed to declare --
# one entry per registered BuildSystemAdapter (esc_exec/adapters.py).
SUPPORTED_BUILD_SYSTEMS = {"gradle", "npm"}

# Every escape-ai-owned generated/managed file lives under this repository-local
# directory, keyed by stable component ID rather than mirroring a component's real
# (expected-to-change) filesystem path. See the plan's "Repository-local Escape AI
# directory" section: escape-ai always resolves a repository through the
# machine-local registry by ID first, then reads a conventional relative path under
# an already-known root -- it never discovers a repository by scanning for these
# files, so nothing requires them to sit at repository/component root.
ESC_AI_DIR = ".esc-ai"
COMPONENTS_DIR = "components"


def repository_manifest_path(root: Path) -> Path:
    """Absolute path to the repository manifest's storage location."""
    return root / ESC_AI_DIR / REPOSITORY_MANIFEST


def repository_manifest_relative_path() -> str:
    """The repository-root-relative path to the repository manifest."""
    return f"{ESC_AI_DIR}/{REPOSITORY_MANIFEST}"


def component_manifest_dir(root: Path, component_id: str) -> Path:
    """
    The manifest bundle directory for a component, flat and keyed by stable
    component ID -- not mirroring `component["path"]` (the component's real,
    expected-to-change source location), which stays `repository_root /
    component["path"]` and is completely unaffected by where the manifest bundle
    itself is stored.
    """
    return root / ESC_AI_DIR / COMPONENTS_DIR / component_id


def component_manifest_path(root: Path, component_id: str) -> Path:
    return component_manifest_dir(root, component_id) / COMPONENT_MANIFEST


def component_manifest_relative_path(component_id: str) -> str:
    """The repository-root-relative path stored in components[].manifest."""
    return f"{ESC_AI_DIR}/{COMPONENTS_DIR}/{component_id}/{COMPONENT_MANIFEST}"


def _merge_generated(existing: dict[str, Any], generated: dict[str, Any]) -> dict[str, Any]:
    result = dict(existing)
    for key, value in generated.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_generated(result[key], value)
        else:
            result[key] = value
    return result


def _architecture_selector_errors(data: dict[str, Any]) -> list[str]:
    architecture = data.get("architecture")
    if architecture is None:
        return []
    if not isinstance(architecture, dict) or set(architecture) - {"profile_ids"}:
        return ["architecture must be a mapping with only a profile_ids key"]
    profile_ids = architecture.get("profile_ids")
    if not isinstance(profile_ids, list) or not profile_ids or not all(isinstance(item, str) and item.strip() for item in profile_ids):
        return ["architecture.profile_ids must be a non-empty list of non-empty strings"]
    return []


def _worktree_inherit_errors(data: dict[str, Any]) -> list[str]:
    """
    Optional repository-manifest field declaring gitignored local files (e.g.
    `local.properties`, `.env`) to copy from the main checkout into every fresh
    task worktree -- see plan/active/pre-flight-doctor-and-gate-prerequisites.md.
    Absent entirely is valid (no inheritance, today's default behavior);
    present-but-malformed is not.
    """
    worktree_inherit = data.get("worktree_inherit")
    if worktree_inherit is None:
        return []
    if not isinstance(worktree_inherit, list) or not all(
        isinstance(item, str) and item.strip() for item in worktree_inherit
    ):
        return ["worktree_inherit must be a list of non-empty strings"]
    return []


# Optional testing/quality-tooling facts on a repository or component manifest --
# see plan/done/manifest-testing-facts-and-documentation-obligation.md. `common`
# facts already cover every platform once declared; `platforms` entries add or
# override facts for a specific platform, matched against a component's own
# detected source sets via that platform's own `source_sets` list (never assumed
# to already be a normalized platform name -- real KMP repositories name their
# per-platform source sets inconsistently, e.g. `iosMain` in most modules but
# `nativeMain` for the same conceptual target in others).
TESTING_FACTS: tuple[str, ...] = ("unit_framework", "mocking_framework", "coverage", "lint_tools", "ui_testing")


def _testing_facts_errors(facts: dict[str, Any], prefix: str) -> list[str]:
    errors: list[str] = []
    for field in ("unit_framework", "mocking_framework"):
        value = facts.get(field)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            errors.append(f"{prefix}.{field} must be a non-empty string")
    lint_tools = facts.get("lint_tools")
    if lint_tools is not None and (
        not isinstance(lint_tools, list) or not all(isinstance(item, str) and item.strip() for item in lint_tools)
    ):
        errors.append(f"{prefix}.lint_tools must be a list of strings (may be empty)")
    coverage = facts.get("coverage")
    if coverage is not None and (
        not isinstance(coverage, dict) or not isinstance(coverage.get("tool"), str) or not coverage["tool"].strip()
    ):
        errors.append(f"{prefix}.coverage must be a mapping with a non-empty tool string")
    ui_testing = facts.get("ui_testing")
    if ui_testing is not None and (
        not isinstance(ui_testing, dict) or not isinstance(ui_testing.get("framework"), str)
        or not ui_testing["framework"].strip()
    ):
        errors.append(f"{prefix}.ui_testing must be a mapping with a non-empty framework string")
    return errors


def _testing_errors(data: dict[str, Any]) -> list[str]:
    """Shape-only validation for the optional `testing` block -- reused at both
    repository and component scope, same as `_architecture_selector_errors`.
    Absent entirely is valid; present-but-malformed is not."""
    testing = data.get("testing")
    if testing is None:
        return []
    if not isinstance(testing, dict) or set(testing) - {"common", "platforms"}:
        return ["testing must be a mapping with only common/platforms keys"]
    errors: list[str] = []
    common = testing.get("common", {})
    if not isinstance(common, dict):
        errors.append("testing.common must be a mapping")
    else:
        errors.extend(_testing_facts_errors(common, "testing.common"))
    platforms = testing.get("platforms", {})
    if not isinstance(platforms, dict):
        errors.append("testing.platforms must be a mapping")
    else:
        for name, declaration in platforms.items():
            if not isinstance(declaration, dict):
                errors.append(f"testing.platforms.{name} must be a mapping")
                continue
            source_sets = declaration.get("source_sets")
            if not isinstance(source_sets, list) or not source_sets or not all(
                isinstance(item, str) and item.strip() for item in source_sets
            ):
                errors.append(f"testing.platforms.{name}.source_sets must be a non-empty list of non-empty strings")
            errors.extend(_testing_facts_errors(declaration, f"testing.platforms.{name}"))
    return errors


def _documentation_errors(data: dict[str, Any]) -> list[str]:
    """
    Optional repository-level documentation location/convention -- see
    plan/done/manifest-testing-facts-and-documentation-obligation.md. Absent
    entirely is valid (a component's own Tier-1-detected `paths.documentation`,
    if any, still applies independently); present-but-malformed is not.
    """
    documentation = data.get("documentation")
    if documentation is None:
        return []
    if not isinstance(documentation, dict) or not isinstance(documentation.get("location"), str) \
            or not documentation["location"].strip():
        return ["documentation must be a mapping with a non-empty location string"]
    convention = documentation.get("convention")
    if convention is not None and (not isinstance(convention, str) or not convention.strip()):
        return ["documentation.convention must be a non-empty string if present"]
    return []


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def merged_testing(repository_manifest: dict[str, Any], component_manifest: dict[str, Any]) -> dict[str, Any]:
    """Component-level `testing` overrides/extends the repository-level default,
    field by field and platform by platform -- same precedent as `architecture.
    profile_ids`, merged via `_deep_merge` rather than a wholesale replace."""
    return _deep_merge(repository_manifest.get("testing") or {}, component_manifest.get("testing") or {})


def component_testing_platforms(testing: dict[str, Any], component_paths: dict[str, Any]) -> list[str]:
    """
    Derives which of `testing.platforms`' declared platforms this component
    actually matches, from its own already-detected `source_*`/`tests_*` path
    keys (see `component_structure`) -- never from a task's declared scope. A
    component matches a platform if any of that platform's declared
    `source_sets` appears among its own detected source-set names; a component
    whose only non-common source sets are internal KMP groupings (e.g.
    `roomMain`, `restMain`) matches no platform at all, correctly, since those
    aren't deployable targets.
    """
    detected = {
        key.split("_", 1)[1] for key in component_paths
        if key.startswith("source_") or key.startswith("tests_")
    }
    if not detected:
        return []
    platforms = testing.get("platforms") or {}
    matched = [
        name for name, declaration in platforms.items()
        if isinstance(declaration, dict) and detected.intersection(declaration.get("source_sets") or [])
    ]
    return sorted(matched)


def resolve_testing_fact(testing: dict[str, Any], fact: str, platform: str | None = None) -> Any:
    """
    Resolves one testing fact for an optional platform context: `testing.
    common.<fact>` first (the shared answer already covers every platform, so
    it wins outright, not merely as a fallback default), else `testing.
    platforms.<platform>.<fact>`, else `None` -- an explicit "nothing known"
    signal, never silently treated as "no testing exists" (see
    `component_testing_gaps` for how callers surface that).
    """
    common = testing.get("common") or {}
    if fact in common:
        return common[fact]
    if platform:
        declaration = (testing.get("platforms") or {}).get(platform) or {}
        if fact in declaration:
            return declaration[fact]
    return None


def component_testing_gaps(testing: dict[str, Any], component_paths: dict[str, Any]) -> list[str]:
    """
    INCOMPLETE-severity gaps (not shape errors): a fact this component's own
    matched platforms declare no answer for at all -- see
    plan/done/manifest-testing-facts-and-documentation-obligation.md's
    Alerting design. Empty for a repository/component that hasn't declared any
    `testing.platforms` at all -- this only checks internal consistency of a
    partially-adopted schema, never "you haven't adopted this feature yet."
    """
    if not testing:
        return []
    gaps = []
    for platform in component_testing_platforms(testing, component_paths):
        for fact in TESTING_FACTS:
            if resolve_testing_fact(testing, fact, platform) is None:
                gaps.append(f"no {fact} known for platform {platform}")
    return gaps


def generate_gradle_manifests(
    root: Path, repository_id: str | None = None, components: list[tuple[str, Path]] | None = None,
) -> list[Path]:
    """
    repository_id/components are optional overrides -- when omitted (every
    existing call site), behavior is unchanged: detect fresh via
    detect_gradle_repository. A caller that already computed a final component
    list (e.g. onboarding merging in Tier 2 AI-resolved components and applying
    exclusions -- see plan/active/generic-multi-component-detection.md) passes it
    through here instead of this function silently re-deriving Tier 1-only
    components and ignoring that work.
    """
    root = root.resolve()
    if repository_id is None or components is None:
        detected_id, detected_components = detect_gradle_repository(root)
        repository_id = repository_id if repository_id is not None else detected_id
        components = components if components is not None else detected_components
    project_paths = gradle_project_paths(root)
    repository_path = repository_manifest_path(root)
    existing_repository = load_yaml(repository_path) if repository_path.exists() else {}
    generated_repository = {
        "schema_version": 1,
        "repository": {
            "id": repository_id,
            "type": "gradle-multi-project",
        },
        "components": [
            {"id": component_id, "manifest": component_manifest_relative_path(component_id)}
            for component_id, relative in components
        ],
        "generation": {
            "generator": "esc-exec",
            "sources": ["settings.gradle.kts" if (root / "settings.gradle.kts").exists() else "settings.gradle"],
        },
    }
    write_yaml(repository_path, _merge_generated(existing_repository, generated_repository))
    written = [repository_path]

    for component_id, relative in components:
        manifest_path = component_manifest_path(root, component_id)
        existing = load_yaml(manifest_path) if manifest_path.exists() else {}
        # project_paths only knows about identifiers this repository's settings
        # file actually declared via include(...) -- an AI-resolved component
        # (not statically parseable at all, see gradle.py's unresolved_gradle_
        # includes) falls back to the directory-derived reconstruction, the best
        # available guess when there's no real declared project path to read.
        project_path = project_paths.get(component_id, ":" + ":".join(relative.parts))
        generated = {
            "schema_version": 1,
            "component": {
                "id": component_id,
                "type": "gradle-module",
                "path": str(relative),
            },
            "build": {
                "system": "gradle",
                "project": project_path,
            },
            "paths": component_structure(root, relative),
            "generation": {
                "generator": "esc-exec",
                "sources": ["settings.gradle.kts" if (root / "settings.gradle.kts").exists() else "settings.gradle"],
            },
        }
        write_yaml(manifest_path, _merge_generated(existing, generated))
        written.append(manifest_path)
    return written


def generate_npm_manifests(
    root: Path, repository_id: str | None = None, components: list[tuple[str, Path]] | None = None,
) -> list[Path]:
    """See generate_gradle_manifests' docstring -- same optional-override contract."""
    root = root.resolve()
    if repository_id is None or components is None:
        detected_id, detected_components = detect_npm_repository(root)
        repository_id = repository_id if repository_id is not None else detected_id
        components = components if components is not None else detected_components
    repository_path = repository_manifest_path(root)
    existing_repository = load_yaml(repository_path) if repository_path.exists() else {}
    generated_repository = {
        "schema_version": 1,
        "repository": {
            "id": repository_id,
            "type": "npm-package",
        },
        "components": [
            {"id": component_id, "manifest": component_manifest_relative_path(component_id)}
            for component_id, relative in components
        ],
        "generation": {
            "generator": "esc-exec",
            "sources": ["package.json"],
        },
    }
    write_yaml(repository_path, _merge_generated(existing_repository, generated_repository))
    written = [repository_path]

    for component_id, relative in components:
        manifest_path = component_manifest_path(root, component_id)
        existing = load_yaml(manifest_path) if manifest_path.exists() else {}
        generated = {
            "schema_version": 1,
            "component": {
                "id": component_id,
                "type": "npm-package",
                "path": str(relative),
            },
            "build": {
                "system": "npm",
            },
            "paths": npm_component_structure(root, relative),
            "generation": {
                "generator": "esc-exec",
                "sources": ["package.json"],
            },
        }
        write_yaml(manifest_path, _merge_generated(existing, generated))
        written.append(manifest_path)
    return written


def validate_repository(root: Path, registry_path: Path | None = None) -> list[ValidationResult]:
    root = root.resolve()
    repository_path = repository_manifest_path(root)
    if not repository_path.exists():
        return [ValidationResult(
            ManifestState.INCOMPLETE,
            str(repository_path),
            [f"Repository manifest is missing. Run: esc-exec manifest generate {root}"],
        )]
    try:
        repository = load_yaml(repository_path)
    except (OSError, ValueError) as exc:
        return [ValidationResult(ManifestState.INVALID, str(repository_path), [str(exc)])]

    results: list[ValidationResult] = []
    repo_messages: list[str] = []
    if repository.get("schema_version") != 1:
        repo_messages.append("schema_version must be 1")
    identity = repository.get("repository")
    if not isinstance(identity, dict):
        repo_messages.append("repository must be a mapping")
    else:
        for field in ("id", "type"):
            if not isinstance(identity.get(field), str) or not identity[field].strip():
                repo_messages.append(f"repository.{field} must be a non-empty string")
    components = repository.get("components")
    if not isinstance(components, list) or not components:
        repo_messages.append("components must be a non-empty list")
        components = []
    repo_messages.extend(_architecture_selector_errors(repository))
    repo_messages.extend(_worktree_inherit_errors(repository))
    repo_messages.extend(_testing_errors(repository))
    repo_messages.extend(_documentation_errors(repository))
    results.append(ValidationResult(
        ManifestState.INVALID if repo_messages else ManifestState.VALID,
        str(repository_path),
        repo_messages,
    ))

    frameworks = repository.get("frameworks")
    if isinstance(frameworks, dict):
        renamed = sorted(fid for fid in frameworks if fid in RENAMED_FRAMEWORK_IDS)
        if renamed and results[0].state == ManifestState.VALID:
            results[0] = ValidationResult(
                ManifestState.STALE,
                str(repository_path),
                [
                    f"frameworks references renamed framework ID `{fid}`; "
                    f"update to `{RENAMED_FRAMEWORK_IDS[fid]}`."
                    for fid in renamed
                ],
            )

    declared_paths: set[str] = set()
    for item in components:
        if not isinstance(item, dict) or not isinstance(item.get("manifest"), str):
            results.append(ValidationResult(
                ManifestState.INVALID,
                str(repository_path),
                ["Every components item must contain string id and manifest fields"],
            ))
            continue
        declared_paths.add(item["manifest"])
        manifest_path = root / item["manifest"]
        results.append(validate_component(
            root, manifest_path, expected_id=item.get("id"), repository_testing=repository.get("testing"),
        ))

    try:
        _, detected, _ = detect_build_system(root)
        detected_paths = {component_manifest_relative_path(component_id) for component_id, _ in detected}
        missing = sorted(detected_paths - declared_paths)
        if missing:
            results[0] = ValidationResult(
                ManifestState.STALE,
                str(repository_path),
                [f"Detected undeclared component manifest: {path}" for path in missing],
            )
    except ValueError:
        pass

    if registry_path is not None:
        compatibility = check_framework_compatibility(repository, registry_path)
        if compatibility.state != ManifestState.VALID:
            precedence = {ManifestState.VALID: 0, ManifestState.INCOMPLETE: 1, ManifestState.STALE: 2, ManifestState.INVALID: 3}
            escalated = max(results[0].state, compatibility.state, key=precedence.__getitem__)
            results[0] = ValidationResult(
                escalated,
                str(repository_path),
                results[0].messages + compatibility.messages,
            )
    return results


def validate_component(
    root: Path, path: Path, expected_id: Any = None, repository_testing: dict[str, Any] | None = None,
) -> ValidationResult:
    """
    `repository_testing` (the repository manifest's own `testing` block, if
    any) is optional and defaults to None for any existing standalone caller --
    only `validate_repository` passes it, since only there is the repository
    manifest already loaded. When given, this component's own detected
    `source_*`/`tests_*` platforms are checked against the merged (repository +
    component) `testing` declaration for facts neither level resolves -- an
    `INCOMPLETE`-severity finding (a declaration gap), never `INVALID` and
    never a hard pre-dispatch blocker. See
    plan/done/manifest-testing-facts-and-documentation-obligation.md.
    """
    if not path.exists():
        return ValidationResult(
            ManifestState.INCOMPLETE,
            str(path),
            ["Declared component manifest is missing; regenerate repository manifests."],
        )
    try:
        data = load_yaml(path)
    except (OSError, ValueError) as exc:
        return ValidationResult(ManifestState.INVALID, str(path), [str(exc)])
    invalid: list[str] = []
    incomplete: list[str] = []
    if data.get("schema_version") != 1:
        invalid.append("schema_version must be 1")
    component = data.get("component")
    if not isinstance(component, dict):
        invalid.append("component must be a mapping")
    else:
        for field in ("id", "type", "path"):
            if not isinstance(component.get(field), str) or not component[field].strip():
                invalid.append(f"component.{field} must be a non-empty string")
        if expected_id and component.get("id") != expected_id:
            invalid.append(f"component.id must match declared id `{expected_id}`")
        if not isinstance(component.get("purpose"), str) or not component.get("purpose", "").strip():
            incomplete.append("component.purpose requires human-authored semantic input")
        component_root = root / component.get("path", "")
        if component.get("path") and not component_root.is_dir():
            return ValidationResult(
                ManifestState.STALE,
                str(path),
                [f"component.path points to missing directory: {component.get('path')}"],
            )
    build = data.get("build")
    if not isinstance(build, dict) or build.get("system") not in SUPPORTED_BUILD_SYSTEMS:
        invalid.append(f"build.system must be one of {sorted(SUPPORTED_BUILD_SYSTEMS)}")
    invalid.extend(_architecture_selector_errors(data))
    invalid.extend(_testing_errors(data))
    if repository_testing is not None:
        testing = _deep_merge(repository_testing or {}, data.get("testing") or {})
        incomplete.extend(component_testing_gaps(testing, data.get("paths") or {}))
    if invalid:
        return ValidationResult(ManifestState.INVALID, str(path), invalid + incomplete)
    if incomplete:
        return ValidationResult(ManifestState.INCOMPLETE, str(path), incomplete)
    return ValidationResult(ManifestState.VALID, str(path), [])


def overall_exit_code(results: list[ValidationResult]) -> int:
    precedence = {
        ManifestState.VALID: 0,
        ManifestState.INCOMPLETE: 2,
        ManifestState.STALE: 3,
        ManifestState.INVALID: 1,
    }
    if any(result.state == ManifestState.INVALID for result in results):
        return 1
    return max((precedence[result.state] for result in results), default=0)

