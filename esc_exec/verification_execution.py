from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from esc_exec.json_io import write_json
from esc_exec.reporting import summarize_junit_reports


DEFAULT_TIMEOUT_SECONDS = 1800


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _not_run_check(check: dict[str, Any], status: str) -> dict[str, Any]:
    return {
        "id": check["id"],
        "command": list(check["command"]),
        "status": status,
        "exit_code": None,
        "duration_ms": None,
        "stdout_path": None,
        "stderr_path": None,
        "report_path": None,
    }


def _log_paths(run_dir: Path, workspace_root: Path, gate_id: str, check_id: str) -> tuple[Path, Path, str, str]:
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_file = logs_dir / f"{gate_id}-{check_id}.stdout.log"
    stderr_file = logs_dir / f"{gate_id}-{check_id}.stderr.log"
    try:
        stdout_relative = str(stdout_file.relative_to(workspace_root))
        stderr_relative = str(stderr_file.relative_to(workspace_root))
    except ValueError as exc:
        raise ValueError("run_dir must be inside workspace_root so log paths stay workspace-relative") from exc
    return stdout_file, stderr_file, stdout_relative, stderr_relative


def _locate_report(
    check: dict[str, Any], workspace_root: Path, run_dir: Path, gate_id: str
) -> str | None:
    """Best-effort JUnit-report enrichment. Never raises: a missing or malformed
    report must not turn an otherwise-verified check result into a crash."""
    report = check.get("report")
    if not report:
        return None
    matches = sorted(workspace_root.glob(report["glob"]))
    if not matches:
        return None
    profile_path = workspace_root / report["profile"]
    output = run_dir / "reports" / f"{gate_id}-{check['id']}-summary.json"
    try:
        summarize_junit_reports(
            matches, profile_path, output,
            full_report_path=str(matches[0].relative_to(workspace_root)),
        )
    except (OSError, ValueError):
        return None
    return str(output.relative_to(workspace_root))


def execute_verification_plan(
    plan: dict[str, Any],
    workspace_root: Path,
    run_dir: Path,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run a verification plan's gate commands directly, independent of any AI adapter.

    Mirrors ``build_verification_plan``'s gate/check shape but produces a
    ``verification-result`` contract instead: real exit codes and durations captured
    by escape-ai's own subprocess call, never the agent's self-report.
    """
    order = plan["strategy"]["order"]
    plan_gates = {gate["id"]: gate for gate in plan["gates"]}
    stopped = False
    result_gates: list[dict[str, Any]] = []
    for gate_id in order:
        gate = plan_gates[gate_id]
        checks = gate.get("checks", [])
        if gate["status"] != "ready":
            result_gates.append({
                "id": gate_id,
                "outcome": "skipped",
                "checks": [_not_run_check(check, "skipped") for check in checks],
            })
            continue
        if stopped:
            result_gates.append({
                "id": gate_id,
                "outcome": "not-run",
                "checks": [_not_run_check(check, "not-run") for check in checks],
            })
            continue
        check_results: list[dict[str, Any]] = []
        for check in checks:
            if stopped:
                check_results.append(_not_run_check(check, "not-run"))
                continue
            stdout_file, stderr_file, stdout_relative, stderr_relative = _log_paths(
                run_dir, workspace_root, gate_id, check["id"]
            )
            command = list(check["command"])
            started = time.monotonic()
            try:
                completed = subprocess.run(
                    command,
                    cwd=workspace_root,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                )
                duration_ms = round((time.monotonic() - started) * 1000)
                stdout_file.write_text(completed.stdout, encoding="utf-8")
                stderr_file.write_text(completed.stderr, encoding="utf-8")
                exit_code = completed.returncode
                status = "passed" if exit_code == 0 else "failed"
            except subprocess.TimeoutExpired as exc:
                duration_ms = round((time.monotonic() - started) * 1000)
                stdout_file.write_text(exc.stdout or "", encoding="utf-8")
                stderr_file.write_text((exc.stderr or "") + "\n[verification gate timed out]", encoding="utf-8")
                exit_code = None
                status = "error"
            except OSError as exc:
                duration_ms = round((time.monotonic() - started) * 1000)
                stdout_file.write_text("", encoding="utf-8")
                stderr_file.write_text(str(exc), encoding="utf-8")
                exit_code = None
                status = "error"
            report_path = None
            if status in {"passed", "failed"}:
                report_path = _locate_report(check, workspace_root, run_dir, gate_id)
            check_results.append({
                "id": check["id"],
                "command": command,
                "status": status,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "stdout_path": stdout_relative,
                "stderr_path": stderr_relative,
                "report_path": report_path,
            })
            if status != "passed":
                stopped = True
        result_gates.append({"id": gate_id, "outcome": "completed", "checks": check_results})
    worst_status = "failed" if any(
        check["status"] in {"failed", "error"}
        for gate in result_gates
        for check in gate["checks"]
    ) else "passed"
    document = {
        "schema_version": 1,
        "task_id": plan["task_id"],
        "generated_at": _now(),
        "status": worst_status,
        "gates": result_gates,
    }
    write_json(run_dir / "verification-result.json", document)
    return document
