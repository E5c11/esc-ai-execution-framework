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
