"""Per-task execution settings read from a task specification's optional `execution` block."""

from typing import Any


def task_timeout_seconds(task_document: dict[str, Any]) -> float | None:
    """
    `execution.timeout_minutes` as seconds, or None when the task does not set it, so the
    agent client's own default applies. The schema bounds it to 1..1440 minutes.
    """
    minutes = (task_document.get("execution") or {}).get("timeout_minutes")
    return float(minutes) * 60 if minutes else None
