from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from esc_exec.manifests import ESC_AI_DIR
from esc_exec.yaml_io import load_yaml, write_yaml


CONVERSATION_ID = re.compile(r"^[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*$")
CONVERSATION_STATUSES = ("in-progress", "converged", "abandoned")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# project_roadmap -- durable, one per repository, updated in place, never appended
# to like a log. See plan/ai-conversation-primitive.md (esc-ai-orchestrator repo) for
# the full design. No formal JSON schema yet -- deliberately deferred, see that
# plan's open question 5 -- so this is plain YAML via yaml_io, not validate_contract,
# unlike checkpoints.py's pattern.
# ---------------------------------------------------------------------------

def project_roadmap_path(repository: Path) -> Path:
    return repository / ESC_AI_DIR / "roadmap.yaml"


def project_roadmap_document(
    repository_id: str,
    purpose: str,
    current_stage: str,
    direction: str,
    durable_decisions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "project_roadmap": {
            "repository": repository_id,
            "updated_at": _now(),
            "purpose": purpose,
            "current_stage": current_stage,
            "direction": direction,
            "durable_decisions": durable_decisions or [],
        },
    }


def save_project_roadmap(
    repository: Path,
    repository_id: str,
    purpose: str,
    current_stage: str,
    direction: str,
    durable_decisions: list[str] | None = None,
) -> Path:
    """
    Always a full overwrite, never an incremental append -- project_roadmap is
    updated in place by design, not a log. Each call replaces the previous document
    wholesale; callers (the compaction step) are responsible for carrying forward
    anything from the prior version that's still durable before calling this.
    """
    path = project_roadmap_path(repository)
    write_yaml(path, project_roadmap_document(repository_id, purpose, current_stage, direction, durable_decisions))
    return path


def load_project_roadmap(repository: Path) -> dict[str, Any] | None:
    path = project_roadmap_path(repository)
    return load_yaml(path) if path.is_file() else None


def roadmap_prompt_line(repository: Path) -> str | None:
    """
    Render this repository's project_roadmap (if any) as one line for an adapter's
    real execution prompt -- see plan/done/project-vision-and-direction.md design 2.
    None when no roadmap has been saved yet, so a caller skips the line entirely
    rather than emitting a hollow placeholder ("Project roadmap: None, None, None").
    Shared by every adapter's _prompt() rather than duplicated three times, since
    they'd otherwise all reimplement the same formatting slightly differently.
    """
    existing = load_project_roadmap(repository)
    if not existing:
        return None
    roadmap = existing.get("project_roadmap", {})
    parts = [
        f"{label}: {roadmap[key]}"
        for label, key in (("purpose", "purpose"), ("current stage", "current_stage"), ("direction", "direction"))
        if roadmap.get(key)
    ]
    decisions = roadmap.get("durable_decisions") or []
    if decisions:
        parts.append(f"durable decisions: {'; '.join(decisions)}")
    if not parts:
        return None
    return f"Project roadmap ({', '.join(parts)})."


# ---------------------------------------------------------------------------
# conversation_summary -- ephemeral, per-conversation. Regenerated wholesale by the
# AI's own compaction judgment each time it's saved, so -- unlike checkpoint's
# accumulate-and-dedup update semantics -- this is always a full replace too, never
# an incremental patch.
# ---------------------------------------------------------------------------

def conversation_summary_path(repository: Path, conversation_id: str) -> Path:
    if not CONVERSATION_ID.fullmatch(conversation_id):
        raise ValueError("conversation ID is not safe for filesystem discovery")
    return repository / ESC_AI_DIR / "conversations" / conversation_id / "summary.yaml"


def conversation_summary_document(
    conversation_id: str,
    purpose: str,
    status: str = "in-progress",
    completed: list[str] | None = None,
    decisions: list[str] | None = None,
    remaining: list[str] | None = None,
    open_questions: list[str] | None = None,
) -> dict[str, Any]:
    if status not in CONVERSATION_STATUSES:
        raise ValueError(f"status must be one of {CONVERSATION_STATUSES}, got {status!r}")
    return {
        "schema_version": 1,
        "conversation_summary": {
            "id": conversation_id,
            "purpose": purpose,
            "status": status,
            "updated_at": _now(),
        },
        "progress": {
            "completed": completed or [],
            "decisions": decisions or [],
            "remaining": remaining or [],
            "open_questions": open_questions or [],
        },
    }


def save_conversation_summary(
    repository: Path,
    conversation_id: str,
    purpose: str,
    status: str = "in-progress",
    completed: list[str] | None = None,
    decisions: list[str] | None = None,
    remaining: list[str] | None = None,
    open_questions: list[str] | None = None,
) -> Path:
    path = conversation_summary_path(repository, conversation_id)
    write_yaml(path, conversation_summary_document(
        conversation_id, purpose, status, completed, decisions, remaining, open_questions,
    ))
    return path


def load_conversation_summary(repository: Path, conversation_id: str) -> dict[str, Any] | None:
    path = conversation_summary_path(repository, conversation_id)
    return load_yaml(path) if path.is_file() else None
