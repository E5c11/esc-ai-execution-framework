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


# Kover's XML report is, in practice, JaCoCo-format-compatible -- verified directly
# against real generated reports from both tools (Kover 0.9.6 in ampm-kmp, JaCoCo in
# ampm-backend): both share the exact same <report>/<package>/<class> structure and
# the same report-level, direct-child <counter type=... missed=... covered=.../>
# vocabulary. One parser covers both; see
# plan/done/coverage-threshold-enforcement.md.
COVERAGE_COUNTER_TYPES = {"INSTRUCTION", "BRANCH", "LINE", "METHOD", "CLASS", "COMPLEXITY"}


def _coverage_profile(path: Path) -> tuple[str, str, float | None]:
    document = load_yaml(path)
    profile = document.get("profile", {})
    limits = document.get("limits", {})
    if document.get("schema_version") != 1:
        raise ValueError("report profile schema_version must be 1")
    if profile.get("format") != "coverage-xml" or not profile.get("id"):
        raise ValueError("report profile must declare an id and coverage-xml format")
    counter_type = limits.get("counter_type", "LINE")
    if counter_type not in COVERAGE_COUNTER_TYPES:
        raise ValueError(f"limits.counter_type must be one of {sorted(COVERAGE_COUNTER_TYPES)}")
    threshold = limits.get("threshold")
    if threshold is not None and (not isinstance(threshold, (int, float)) or threshold < 0):
        raise ValueError("limits.threshold must be a non-negative number if present")
    return profile["id"], counter_type, threshold


def summarize_coverage_report(
    source: Path,
    profile_path: Path,
    output: Path,
    full_report_path: str | None = None,
) -> dict[str, Any]:
    """
    Parses a JaCoCo-format coverage XML report (produced by JaCoCo directly, or by
    Kover, which emits the same schema) and compares its report-level, whole-module
    coverage percentage against an optional declared threshold. `status` is
    `"passed"` unconditionally when no threshold is declared (report-only,
    informational) or when the percentage meets the threshold; `"failed"` otherwise
    -- the tool's own report-generation command exits 0 regardless of coverage
    level, so this is the actual enforcement, not the subprocess exit code.
    """
    profile_id, counter_type, threshold = _coverage_profile(profile_path)
    report_path = full_report_path or source.name
    if Path(report_path).is_absolute():
        raise ValueError("full report path must be workspace-relative")
    try:
        root = ET.parse(source).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"invalid coverage XML: {exc}") from exc
    if root.tag != "report":
        raise ValueError("coverage XML root must be report")
    # Only the report's own direct-child counters are the whole-module totals --
    # the same-named counters nested inside <package>/<class>/<sourcefile> are
    # finer-grained and not what "overall coverage" means here.
    counter = next(
        (child for child in root if child.tag == "counter" and child.get("type") == counter_type),
        None,
    )
    if counter is None:
        raise ValueError(f"coverage XML has no report-level counter of type {counter_type}")
    try:
        missed, covered = int(counter.get("missed", "")), int(counter.get("covered", ""))
    except ValueError as exc:
        raise ValueError("coverage XML counter missed/covered must be integers") from exc
    total = missed + covered
    percent = round((covered / total) * 100, 2) if total else 100.0
    met = threshold is None or percent >= threshold
    status = "passed" if met else "failed"
    document = {
        "schema_version": 1,
        "coverage": {
            "profile": profile_id,
            "source_format": "coverage-xml",
            "status": status,
            "generated_at": _now(),
        },
        "totals": {"counter_type": counter_type, "missed": missed, "covered": covered, "percent": percent},
        "threshold": {"required": threshold, "met": met},
        "full_report": {"path": report_path, "media_type": "application/xml"},
    }
    write_json(output, document)
    return document


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
