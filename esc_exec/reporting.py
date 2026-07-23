from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from esc_exec.json_io import write_json
from esc_exec.yaml_io import load_yaml


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _profile(path: Path) -> tuple[str, int, int]:
    document = load_yaml(path)
    profile = document.get("profile", {})
    limits = document.get("limits", {})
    if document.get("schema_version") != 1:
        raise ValueError("report profile schema_version must be 1")
    if profile.get("format") != "junit-xml" or not profile.get("id"):
        raise ValueError("report profile must declare an id and junit-xml format")
    max_failures = limits.get("max_failures")
    max_message_chars = limits.get("max_message_chars")
    if not isinstance(max_failures, int) or not 0 <= max_failures <= 100:
        raise ValueError("limits.max_failures must be between 0 and 100")
    if not isinstance(max_message_chars, int) or not 1 <= max_message_chars <= 4000:
        raise ValueError("limits.max_message_chars must be between 1 and 4000")
    return profile["id"], max_failures, max_message_chars


def summarize_junit(
    source: Path,
    profile_path: Path,
    output: Path,
    full_report_path: str | None = None,
) -> dict[str, Any]:
    return summarize_junit_reports([source], profile_path, output, full_report_path)


def summarize_junit_reports(
    sources: list[Path],
    profile_path: Path,
    output: Path,
    full_report_path: str | None = None,
) -> dict[str, Any]:
    """Aggregate one or more JUnit XML files (e.g. Gradle's one-file-per-class output)
    into a single validated verification-summary. A single source behaves exactly
    like the original single-file `summarize_junit`."""
    if not sources:
        raise ValueError("summarize_junit_reports requires at least one source file")
    profile_id, max_failures, max_message_chars = _profile(profile_path)
    report_path = full_report_path or sources[0].name
    if Path(report_path).is_absolute():
        raise ValueError("full report path must be workspace-relative")

    tests = failed = errors = skipped = duration_ms = 0
    all_failures: list[dict[str, str]] = []
    for source in sources:
        try:
            root = ET.parse(source).getroot()
        except ET.ParseError as exc:
            raise ValueError(f"invalid JUnit XML: {exc}") from exc
        if root.tag not in {"testsuite", "testsuites"}:
            raise ValueError("JUnit XML root must be testsuite or testsuites")
        for case in root.iter("testcase"):
            tests += 1
            try:
                duration_ms += round(float(case.get("time", "0")) * 1000)
            except ValueError:
                pass
            skipped_node = case.find("skipped")
            failure_node = case.find("failure")
            error_node = case.find("error")
            if skipped_node is not None:
                skipped += 1
                continue
            problem = error_node if error_node is not None else failure_node
            if problem is None:
                continue
            kind = "error" if error_node is not None else "failure"
            errors += kind == "error"
            failed += kind == "failure"
            raw_message = problem.get("message") or problem.text or ""
            message = " ".join(raw_message.split())[:max_message_chars]
            item = {
                "suite": case.get("classname", ""),
                "test": case.get("name", "unknown"),
                "kind": kind,
                "message": message,
            }
            if not item["suite"]:
                item.pop("suite")
            all_failures.append(item)

    status = "error" if errors else "failed" if failed else "passed"
    included = all_failures[:max_failures]
    document = {
        "schema_version": 1,
        "verification": {
            "profile": profile_id,
            "source_format": "junit-xml",
            "status": status,
            "generated_at": _now(),
        },
        "totals": {
            "tests": tests,
            "passed": tests - failed - errors - skipped,
            "failed": failed,
            "errors": errors,
            "skipped": skipped,
            "duration_ms": duration_ms,
        },
        "failures": included,
        "truncation": {
            "failures_included": len(included),
            "failures_omitted": len(all_failures) - len(included),
            "max_failures": max_failures,
            "max_message_chars": max_message_chars,
        },
        "full_report": {"path": report_path, "media_type": "application/xml"},
    }
    write_json(output, document)
    return document
