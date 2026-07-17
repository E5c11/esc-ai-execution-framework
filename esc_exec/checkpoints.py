from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from esc_exec.contracts import validate_contract
from esc_exec.model import ManifestState
from esc_exec.yaml_io import load_yaml, write_yaml


TASK_ID = re.compile(r"^[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*$")
CHECKPOINT_ROOT = Path("workflows/active")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def checkpoint_path(repository: Path, task_id: str) -> Path:
    if not TASK_ID.fullmatch(task_id):
        raise ValueError("task ID is not safe for checkpoint discovery")
    return repository / CHECKPOINT_ROOT / task_id / "checkpoint.yaml"


def checkpoint_document(
    task_document: dict[str, Any],
    run_id: str | None = None,
    status: str = "active",
    task_path: str | None = None,
    completed: list[str] | None = None,
    decisions: list[str] | None = None,
    remaining: list[str] | None = None,
    blockers: list[str] | None = None,
    artifacts: list[str] | None = None,
    last_event_sequence: int | None = None,
) -> dict[str, Any]:
    task = task_document["task"]
    checkpoint: dict[str, Any] = {
        "id": f"checkpoint-{task['id']}",
        "task_id": task["id"],
        "status": status,
        "objective": task["objective"],
        "updated_at": _now(),
    }
    if run_id:
        checkpoint["run_id"] = run_id
    if task_path:
        checkpoint["task_path"] = task_path
    progress: dict[str, Any] = {
        "completed": completed or [],
        "decisions": decisions or [],
        "remaining": remaining or [],
        "blockers": blockers or [],
        "artifacts": artifacts or [],
    }
    if last_event_sequence is not None:
        progress["last_event_sequence"] = last_event_sequence
    return {"schema_version": 1, "checkpoint": checkpoint, "progress": progress}


def create_checkpoint(
    repository: Path,
    task_path: Path,
    **progress: Any,
) -> Path:
    repository = repository.resolve()
    task_path = task_path.resolve()
    try:
        relative_task = task_path.relative_to(repository)
    except ValueError as exc:
        raise ValueError("task specification must be committed inside the repository") from exc
    task_result = validate_contract("task", task_path)
    if task_result.state != ManifestState.VALID:
        raise ValueError(f"invalid task specification: {'; '.join(task_result.messages)}")
    task_document = load_yaml(task_path)
    output = checkpoint_path(repository, task_document["task"]["id"])
    if output.exists():
        raise ValueError(f"checkpoint already exists: {output}; use checkpoint update")
    document = checkpoint_document(task_document, task_path=str(relative_task), **progress)
    write_yaml(output, document)
    result = validate_contract("checkpoint", output)
    if result.state != ManifestState.VALID:
        output.unlink(missing_ok=True)
        raise ValueError(f"invalid checkpoint: {'; '.join(result.messages)}")
    return output


def update_checkpoint(
    repository: Path,
    task_id: str,
    status: str | None = None,
    run_id: str | None = None,
    completed: list[str] | None = None,
    decisions: list[str] | None = None,
    remaining: list[str] | None = None,
    blockers: list[str] | None = None,
    artifacts: list[str] | None = None,
    clear_blockers: bool = False,
    last_event_sequence: int | None = None,
) -> Path:
    path = checkpoint_path(repository.resolve(), task_id)
    if not path.is_file():
        raise ValueError(f"checkpoint does not exist: {path}")
    original = load_yaml(path)
    document = load_yaml(path)
    checkpoint = document["checkpoint"]
    progress = document["progress"]
    if status:
        checkpoint["status"] = status
    if run_id:
        checkpoint["run_id"] = run_id
    checkpoint["updated_at"] = _now()
    if clear_blockers:
        progress["blockers"] = []
    for key, values in (
        ("completed", completed), ("decisions", decisions), ("remaining", remaining),
        ("blockers", blockers), ("artifacts", artifacts),
    ):
        for value in values or []:
            if value not in progress.setdefault(key, []):
                progress[key].append(value)
    if last_event_sequence is not None:
        progress["last_event_sequence"] = last_event_sequence
    write_yaml(path, document)
    result = validate_contract("checkpoint", path)
    if result.state != ManifestState.VALID:
        write_yaml(path, original)
        raise ValueError(f"invalid checkpoint update: {'; '.join(result.messages)}")
    return path


def inspect_checkpoint(repository: Path, task_id: str) -> str:
    path = checkpoint_path(repository.resolve(), task_id)
    result = validate_contract("checkpoint", path)
    if result.state != ManifestState.VALID:
        raise ValueError("; ".join(result.messages))
    return json.dumps(load_yaml(path), separators=(",", ":"), ensure_ascii=False)
