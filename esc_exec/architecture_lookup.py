from __future__ import annotations

import json
from pathlib import Path
from typing import Any


LAYER_ORDER = {
    "core": 0,
    "patterns": 1,
    "architectures": 2,
    "platforms": 3,
    "build": 4,
    "quality-gates": 5,
    "feature-orchestrators": 6,
}


def load_architecture_index(framework_root: Path) -> dict[str, dict[str, Any]]:
    index_path = framework_root / "index.json"
    if not index_path.is_file():
        raise FileNotFoundError(f"Architecture framework index not found: {index_path}")
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid architecture framework index at {index_path}: {exc}") from exc
    documents = data.get("documents")
    if not isinstance(documents, list):
        raise ValueError(f"Architecture framework index at {index_path} is missing a 'documents' list")
    index: dict[str, dict[str, Any]] = {}
    for document in documents:
        doc_id = document.get("id") if isinstance(document, dict) else None
        if isinstance(doc_id, str) and doc_id:
            index[doc_id] = document
    return index


def resolve_architecture_docs(
    doc_ids: list[str], index: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Topologically resolve one or more architecture-framework document IDs against a
    loaded index, returning (ordered documents, missing IDs).

    Each seed's `requires` chain is visited depth-first before the seed itself is
    included (matching the architecture framework's own tools/lookup.py), then the
    merged result is sorted by layer. Unlike that single-seed original, resolved seeds
    are not forced to the end here: with multiple seeds spanning different layers,
    forcing every seed last would fight the layer ordering itself (a core-layer seed
    belongs before an architecture-layer dependent, not after it). Layer ordering alone
    already puts feature-orchestrator-layer entry points last.

    Doc IDs absent from the index are reported back rather than silently dropped.
    """
    visited: set[str] = set()
    ordered_ids: list[str] = []
    missing: list[str] = []

    def visit(doc_id: str) -> None:
        if doc_id in visited:
            return
        visited.add(doc_id)
        document = index.get(doc_id)
        if document is None:
            if doc_id not in missing:
                missing.append(doc_id)
            return
        for dependency in document.get("requires") or []:
            visit(dependency)
        ordered_ids.append(doc_id)

    for doc_id in doc_ids:
        visit(doc_id)

    ordered_ids.sort(key=lambda doc_id: LAYER_ORDER.get(index[doc_id].get("layer", ""), 99))
    return [index[doc_id] for doc_id in ordered_ids], missing


def stub_documents(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Return the subset of resolved documents whose status is 'stub'. The Gap Protocol
    applies to these; callers must not treat them as fully specified.
    """
    return [document for document in documents if document.get("status") == "stub"]


def load_profile_doc_map(framework_root: Path) -> dict[str, Any]:
    """
    Read the architecture framework's generated profile-doc-map.json: a data-only
    export of its tools/lookup.py::PROFILE_DOC_MAP/TARGET_DOC_MAP, shaped as
    {"frameworks": {field: {value: [doc_id, ...]}}, "targets": {target: [doc_id, ...]}}.
    """
    path = framework_root / "profile-doc-map.json"
    if not path.is_file():
        raise FileNotFoundError(f"Architecture framework profile-doc-map not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid architecture framework profile-doc-map at {path}: {exc}") from exc


NEXTJS_GENERIC_PROFILE_ID = "PLAT-WEB-NEXT"
NEXTJS_WEB_APP_PROFILE_ID = "PLAT-WEB-NEXT-APP"


def refine_profile_ids_for_style(suggested: list[str], architecture_style: str | None) -> list[str]:
    """
    Apply the one confirmed architecture_style-driven refinement (see
    plan/active/npm-architecture-profile-detection.md design section 2) to an
    already-resolved profile-id list -- factored out so both `suggest_profile_ids`
    (fresh frameworks/targets) and `apply_onboarding_answers`'s analyze-time-
    suggestion path (plan/active/apply-time-profile-id-suggestion-gap.md, which
    carries a list forward without re-deriving it from frameworks/targets) apply
    the same rule, instead of only the first one doing so and the second silently
    never seeing a separately-answered architecture_style at all.

    `architecture_style == "web-content"` is deliberately not handled -- no
    confirmed doc ID for a `web-content`-specific Next.js extension exists yet
    (open question 2 in the plan above); adding one would be a guess, not a
    verified mapping.
    """
    if (
        architecture_style == "web-app"
        and NEXTJS_GENERIC_PROFILE_ID in suggested
        and NEXTJS_WEB_APP_PROFILE_ID not in suggested
    ):
        return [*suggested, NEXTJS_WEB_APP_PROFILE_ID]
    return suggested


def suggest_profile_ids(
    frameworks: dict[str, str], targets: list[str], profile_doc_map: dict[str, Any],
    architecture_style: str | None = None,
) -> list[str]:
    """
    Suggest architecture.profile_ids from declared/detected frameworks and targets,
    mirroring the architecture framework's own tools/lookup.py::profile_extra_docs.
    Order is first-seen, targets before frameworks, deduplicated.

    `architecture_style` is optional and strictly additive (see
    plan/active/npm-architecture-profile-detection.md design section 2): when
    absent/None, behavior is identical to before this parameter existed. A bare
    "uses Next.js" signal resolves only the generic `PLAT-WEB-NEXT` doc -- it
    can't tell `web-app` (forms/Server-Actions-heavy) apart from `web-content`
    (SSG/ISR-heavy) on its own. See `refine_profile_ids_for_style` for the actual
    refinement rule.
    """
    suggested: list[str] = []
    target_map = profile_doc_map.get("targets", {})
    for target in targets:
        for doc_id in target_map.get(target, []):
            if doc_id not in suggested:
                suggested.append(doc_id)
    framework_map = profile_doc_map.get("frameworks", {})
    for field, value in frameworks.items():
        for doc_id in framework_map.get(field, {}).get(value, []):
            if doc_id not in suggested:
                suggested.append(doc_id)
    return refine_profile_ids_for_style(suggested, architecture_style)
