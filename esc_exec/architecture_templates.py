"""Starter architecture profiles: rules a team would otherwise write by hand.

A template is not inferred from the code. It is the mechanical half of an architecture's dependency
rules, written once and checked against the project's own folders (``profile_from_template``). The
author still reads the result, because folder layout is the one input a template cannot know.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


WEB_APP = "web-app"
TEMPLATES = (WEB_APP,)

# Provider SDK families that a UI layer must not import directly. A project's own dependencies are added
# to this list; a name that is not installed does no harm.
_DEFAULT_PROVIDER_SDKS = ("firebase", "firebase-admin")
_KNOWN_PROVIDER_SDKS = ("pg", "mysql2", "mongodb", "@prisma/client", "undici", "axios", "node-fetch", "@supabase/supabase-js")
_PROVIDER_SDK_PREFIXES = ("@firebase/", "@google-cloud/")

_FRAMEWORK_IMPORTS = ["react", "react/**", "react-dom", "react-dom/**", "next", "next/**"]


def _dependencies(component_root: Path) -> set[str]:
    package = component_root / "package.json"
    if not package.is_file():
        return set()
    try:
        document = json.loads(package.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    names: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        section = document.get(key)
        if isinstance(section, dict):
            names.update(name for name in section if isinstance(name, str))
    return names


def _provider_sdks(component_root: Path) -> list[str]:
    installed = _dependencies(component_root)
    names = set(_DEFAULT_PROVIDER_SDKS)
    names.update(name for name in installed if name in _KNOWN_PROVIDER_SDKS or name.startswith(_PROVIDER_SDK_PREFIXES))
    patterns: list[str] = []
    for name in sorted(names):
        patterns.extend([name, f"{name}/**"])
    return patterns


def source_base(component_root: Path) -> str:
    """``src/`` when the project keeps its folders under ``src``, otherwise the component root."""
    if (component_root / "src/features").is_dir() or (component_root / "src/app").is_dir():
        return "src/"
    return ""


_GRAB_BAG_NAMES = ("utils", "helpers", "common", "misc", "base", "lib")


def _grab_bag_patterns(base: str) -> list[str]:
    """Folders and files with a name that says nothing, under the source roots (never node_modules)."""
    roots = ["src"] if base else ["app", "features", "shared", "composition", "data"]
    patterns: list[str] = []
    for root in roots:
        for name in _GRAB_BAG_NAMES:
            patterns += [f"{root}/**/{name}", *(f"{root}/**/{name}{ext}" for ext in (".ts", ".tsx", ".js", ".jsx"))]
    return patterns


def _web_app(component_root: Path) -> dict[str, Any]:
    base = source_base(component_root)
    features = f"{base}features/*"

    def layer(name: str) -> str:
        return f"{features}/{name}"

    shared = f"{base}shared"
    app = f"{base}app"
    app_path = app
    ui_layers = [layer("components"), layer("hooks"), layer("useCases"), layer("domain")]
    # The server runtime's Server Actions are a layer of their own: a host that calls use cases, below the UI.
    hosts = [*ui_layers, layer("actions")]
    return {
        "pattern_sets": {
            "provider-sdks": _provider_sdks(component_root),
            "framework": _FRAMEWORK_IMPORTS,
            "data-implementations": [
                "**/data/Firestore*", "**/data/Http*", "**/data/Callable*", "**/data/Default*", "**/data/InMemory*",
            ],
            "feature-internals": [
                "**/features/*/components/**", "**/features/*/hooks/**", "**/features/*/useCases/**",
                "**/features/*/domain/**", "**/features/*/data/**",
            ],
        },
        "rules": [
            {
                "id": "web.layout-present",
                "type": "required-path",
                "description": (
                    "The folders the other rules check exist. If this fails, the layout is not the one the "
                    "template assumes and the remaining rules are checking nothing."
                ),
                "paths": [f"{base}features", app_path],
            },
            {
                "id": "web.domain-is-pure",
                "type": "forbidden-import",
                "optional": True,
                "description": "ARCH-WEB-APP-DEP-01: domain code imports no framework, no provider SDK and no other layer.",
                "source_roots": [layer("domain"), f"{shared}/domain"],
                "patterns": [
                    "{framework}", "{provider-sdks}",
                    "**/hooks/**", "**/data/**", "**/components/**", "**/useCases/**", "**/composition/**", "**/app/**",
                ],
            },
            {
                "id": "web.usecases-depend-on-interfaces",
                "type": "forbidden-import",
                "optional": True,
                "description": "ARCH-WEB-APP-DEP-02: UseCases import no framework, no provider SDK and no DataSource implementation.",
                "source_roots": [layer("useCases")],
                "patterns": ["{framework}", "{provider-sdks}", "{data-implementations}"],
            },
            {
                "id": "web.sdk-only-in-datasources",
                "type": "forbidden-import",
                "optional": True,
                "description": "ARCH-WEB-APP-DEP-03: a provider SDK is imported only by DataSources and the composition module.",
                "source_roots": [*hosts, shared, app],
                "patterns": ["{provider-sdks}"],
            },
            {
                "id": "web.hooks-no-implementations",
                "type": "forbidden-import",
                "optional": True,
                "description": "ARCH-WEB-APP-DEP-04: a Hook imports no provider SDK and no DataSource or Repository implementation.",
                "source_roots": [layer("hooks")],
                "patterns": ["{provider-sdks}", "{data-implementations}"],
            },
            {
                "id": "web.components-nothing-below-hooks",
                "type": "forbidden-import",
                "optional": True,
                "description": (
                    "ARCH-WEB-APP-DEP-05: a Component imports nothing below the Hook. "
                    "A {Feature}Page.tsx Server Component shell is exempt (ARCH-WEB-APP-COMP-SERVER-01)."
                ),
                "source_roots": [layer("components"), f"{shared}/ui"],
                "exclude": ["**/*Page.tsx"],
                "patterns": ["{provider-sdks}", "**/data/**", "**/useCases/**", "**/domain/**", "**/composition/**"],
            },
            {
                "id": "web.implementations-only-in-composition",
                "type": "forbidden-import",
                "optional": True,
                "description": "ARCH-WEB-APP-DEP-06: concrete DataSources and Repositories are imported only by the composition module and tests.",
                "source_roots": [*hosts, shared, app],
                "patterns": ["{data-implementations}"],
            },
            {
                "id": "web.shared-data-only-for-datasources",
                "type": "forbidden-import",
                "optional": True,
                "description": "ARCH-WEB-APP-FEAT-DATA-01: code shared by DataSources lives in the top-level data/ folder, which only DataSources and the composition module import.",
                "source_roots": [*hosts, shared, app],
                "patterns": [f"{base}data/**"],
            },
            {
                "id": "web.actions-below-the-ui",
                "type": "forbidden-import",
                "optional": True,
                "description": "ARCH-WEB-APP-DEP-04: a Server Action is a host for a use case; it imports no component and no hook.",
                "source_roots": [layer("actions")],
                "patterns": ["**/components/**", "**/hooks/**"],
            },
            {
                "id": "web.server-graph-not-in-client-code",
                "type": "forbidden-import",
                "optional": True,
                "description": (
                    "ARCH-WEB-APP-COMPOSE-SPLIT-01: the server graph is imported by Server Actions, routes and Server Component "
                    "shells, never by a hook or a shared module a Client Component may use."
                ),
                "source_roots": [layer("hooks"), shared],
                "patterns": ["**/composition/server*"],
            },
            {
                "id": "web.server-graph-imports-server-only",
                "type": "required-import",
                "optional": True,
                "description": "PLAT-WEB-NEXT-SERVERONLY-01: the module that holds the server graph imports `server-only`, so importing it from client code fails the build.",
                "files": [f"{base}composition/server*.ts"],
                "patterns": ["server-only"],
            },
            {
                "id": "web.no-grab-bag-names",
                "type": "forbidden-path",
                "description": "ARCH-WEB-APP-MOD-NAME-01: no module, folder or package is named utils, helpers, common, misc, base or lib; name code for the concept it implements.",
                "patterns": _grab_bag_patterns(base),
            },
            {
                "id": "web.features-through-their-index",
                "type": "forbidden-import",
                "optional": True,
                "description": "ARCH-WEB-APP-DEP-07: a feature is imported only through its public surface, never by its internals.",
                "source_roots": [features, app],
                "patterns": ["{feature-internals}"],
                "except_own_feature": True,
            },
        ],
    }


def profile_from_template(template: str, component_root: Path) -> dict[str, Any]:
    """The ``rules`` (and ``pattern_sets``) a profile starts with for ``template``."""
    if template == WEB_APP:
        return _web_app(component_root)
    raise ValueError(f"unknown architecture template: {template} (known: {', '.join(TEMPLATES)})")
