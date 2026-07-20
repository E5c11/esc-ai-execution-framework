from __future__ import annotations

import json
from pathlib import Path


def detect_npm_repository(root: Path) -> tuple[str, list[tuple[str, Path]]]:
    """
    Deliberately single-component only -- no npm/yarn/pnpm workspace (monorepo)
    detection. A real workspace-aware adapter is a legitimate future extension, not
    something to half-build speculatively here.
    """
    package_json = root / "package.json"
    if not package_json.is_file():
        raise ValueError(f"No package.json found under {root}")
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except ValueError:
        data = {}
    name = data.get("name") if isinstance(data, dict) else None
    repository_id = name if isinstance(name, str) and name.strip() else root.name
    return repository_id, [(repository_id, Path("."))]


def npm_component_structure(root: Path, relative: Path) -> dict[str, str]:
    component = root / relative
    candidates = {
        "source": component / "src",
        "tests": component / "__tests__",
        "documentation": component / "README.md",
        "build": component / "package.json",
    }
    paths = {
        key: str(path.relative_to(component))
        for key, path in candidates.items()
        if path.exists()
    }
    if "source" not in paths:
        # Next.js App Router (app/) and Pages Router (pages/) repositories without a
        # src/ directory keep routes directly at the component root. Mirrors
        # gradle.py's KMP source-set handling: use the source_ prefix so indexing.py's
        # search_roots filter (which only matches "source"/"tests" or the
        # source_*/tests_* prefixes) still picks these up.
        for name in ("app", "pages"):
            if (component / name).is_dir():
                paths[f"source_{name}"] = name
    return paths
