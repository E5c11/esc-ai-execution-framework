from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from esc_exec.json_io import load_json, write_json


DIMENSIONS = {
    "tokens": ("tokens", "total"),
    "elapsed_ms": ("execution", "elapsed_ms"),
    "tool_calls": ("execution", "tool_calls"),
    "context_bytes": ("context", "bytes"),
    "rework_events": ("execution", "rework_events"),
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def token_metrics(response: dict[str, Any]) -> dict[str, Any]:
    raw = response.get("info", {}).get("tokens")
    if not isinstance(raw, dict):
        return {
            "status": "unavailable", "input": None, "output": None,
            "reasoning": None, "cache_read": None, "cache_write": None, "total": None,
        }
    cache = raw.get("cache", {}) if isinstance(raw.get("cache"), dict) else {}
    values = {
        "input": raw.get("input"),
        "output": raw.get("output"),
        "reasoning": raw.get("reasoning", 0),
        "cache_read": cache.get("read", raw.get("cache_read", 0)),
        "cache_write": cache.get("write", raw.get("cache_write", 0)),
    }
    if not all(isinstance(value, int) and value >= 0 for value in values.values()):
        return {
            "status": "unavailable", "input": None, "output": None,
            "reasoning": None, "cache_read": None, "cache_write": None, "total": None,
        }
    return {"status": "reported", **values, "total": values["input"] + values["output"] + values["reasoning"]}


def run_metrics(
    run_id: str,
    task_id: str,
    provider: str,
    status: str,
    context_path: Path,
    context: dict[str, Any],
    elapsed_ms: int,
    tools: list[dict[str, Any]],
    response: dict[str, Any],
    rework_events: int = 0,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run": {"id": run_id, "task_id": task_id, "provider": provider, "status": status},
        "context": {
            "bytes": context_path.stat().st_size,
            "components": len(context["routing"]["components"]),
            "paths": len(context["scope"]["paths"]),
            "references": len(context["scope"]["references"]),
        },
        "execution": {
            "elapsed_ms": max(elapsed_ms, 0),
            "tool_calls": len(tools),
            "read_calls": sum(tool.get("tool") == "read" for tool in tools),
            "agent_messages": 1 if status == "succeeded" else 0,
            "rework_events": max(rework_events, 0),
        },
        "tokens": token_metrics(response),
        "generated_at": now(),
    }


def _value(document: dict[str, Any], path: tuple[str, str]) -> int | None:
    value = document.get(path[0], {}).get(path[1])
    return value if isinstance(value, int) and value >= 0 else None


def compare_efficiency(
    baseline_paths: list[Path],
    candidate_paths: list[Path],
    output: Path,
) -> dict[str, Any]:
    if not baseline_paths or not candidate_paths:
        raise ValueError("at least one baseline and candidate metrics file is required")
    baseline = [load_json(path) for path in baseline_paths]
    candidate = [load_json(path) for path in candidate_paths]
    dimensions = {}
    for name, path in DIMENSIONS.items():
        baseline_values = [value for document in baseline if (value := _value(document, path)) is not None]
        candidate_values = [value for document in candidate if (value := _value(document, path)) is not None]
        if not baseline_values or not candidate_values:
            dimensions[name] = {
                "status": "unavailable", "baseline_average": None, "candidate_average": None,
                "change_percent": None, "baseline_samples": len(baseline_values),
                "candidate_samples": len(candidate_values),
            }
            continue
        baseline_average = mean(baseline_values)
        candidate_average = mean(candidate_values)
        change = None if baseline_average == 0 else round((baseline_average - candidate_average) / baseline_average * 100, 2)
        direction = "unchanged" if candidate_average == baseline_average else "improved" if candidate_average < baseline_average else "regressed"
        dimensions[name] = {
            "status": direction,
            "baseline_average": round(baseline_average, 2),
            "candidate_average": round(candidate_average, 2),
            "change_percent": change,
            "baseline_samples": len(baseline_values),
            "candidate_samples": len(candidate_values),
        }
    document = {
        "schema_version": 1,
        "baseline_runs": [document["run"]["id"] for document in baseline],
        "candidate_runs": [document["run"]["id"] for document in candidate],
        "dimensions": dimensions,
        "generated_at": now(),
    }
    write_json(output, document)
    return document
