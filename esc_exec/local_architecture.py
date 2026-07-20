from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


# See plan/active/planning-consistency-checks.md design section 2. Local, never
# in the shared esc-ai-architecture-framework checkout -- these notes exist so a
# repository's own planning has something concrete to reference when the real
# framework doesn't yet cover an objective, not so they become authoritative.
LOCAL_ARCHITECTURE_DIR = ".esc-ai/local-architecture"


def local_architecture_note_path(root: Path, slug: str) -> Path:
    return root / LOCAL_ARCHITECTURE_DIR / f"{slug}.md"


def write_local_architecture_note(
    root: Path, slug: str, *, doc_id: str, doc_type: str, layer: str,
    platform: list[str], architecture: list[str], title: str, body: str,
    requires: list[str] | None = None, related: list[str] | None = None,
    tags: list[str] | None = None,
) -> Path:
    """
    Writes a local, unreviewed architecture note -- same frontmatter shape as
    esc-ai-architecture-framework's own document.yaml schema (id/type/layer/
    platform/architecture/requires/related/tags/status), verified directly
    against that schema, not assumed -- so promoting one later through
    framework-contribution-and-extensibility.md's flow is a lift-and-drop into
    the real repo's directory structure, not a rewrite.

    status is always "stub" here, never "active" -- nothing local is reviewed,
    and this must never look indistinguishable from a real, trusted framework
    document to anything reading it later.

    doc_id is not validated against document.yaml's real namespace-prefix-
    per-layer pattern (`^[A-Z]+-[A-Z0-9-]+$`) -- that constraint only matters
    once merged into the framework's own index; while local, it just needs to
    be a stable identifier for this note within this repository.
    """
    frontmatter = {
        "id": doc_id,
        "type": doc_type,
        "layer": layer,
        "platform": platform,
        "architecture": architecture,
        "requires": requires or [],
        "related": related or [],
        "tags": tags or [],
        "status": "stub",
    }
    rendered = "\n".join([
        "---",
        yaml.safe_dump(frontmatter, sort_keys=False, default_flow_style=None).strip(),
        "---",
        "",
        f"# {title}",
        "",
        body.strip(),
        "",
    ])
    path = local_architecture_note_path(root, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")
    return path


def read_local_architecture_note_frontmatter(path: Path) -> dict[str, Any]:
    """
    Reads back just the frontmatter block a local note was written with --
    used by whatever later reads "which local notes exist and what do they
    cover" (a future promotion flow, or a repeat coverage check that shouldn't
    re-draft a note that already exists for the same objective). Returns {} for
    anything that doesn't parse as a frontmatter-delimited file -- fails open,
    never raises, matching this codebase's discipline for any read of a
    hand-editable file.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    try:
        data = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def list_local_architecture_notes(root: Path) -> list[Path]:
    directory = root / LOCAL_ARCHITECTURE_DIR
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.md"))
