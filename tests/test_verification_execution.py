from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest

from esc_exec.contracts import validate_contract
from esc_exec.model import ManifestState
from esc_exec.verification_execution import execute_verification_plan


def _ok_command() -> list[str]:
    return [sys.executable, "-c", "import sys; sys.exit(0)"]


def _failing_command() -> list[str]:
    return [sys.executable, "-c", "import sys; sys.exit(1)"]


def _plan(gates: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "task_id": "task-index-review",
        "profiles": [],
        "strategy": {"order": ["focused", "component", "impact", "final"], "stop_on_failure": True},
        "impact": {"graph": "esc-dependencies.json", "source_components": [], "consumer_components": []},
        "gates": gates,
    }


class ExecuteVerificationPlanTests(unittest.TestCase):
    def test_all_gates_pass_produces_valid_result(self):
        plan = _plan([
            {"id": "focused", "status": "not-applicable", "checks": []},
            {"id": "component", "status": "ready", "checks": [{"id": "content-tests", "command": _ok_command()}]},
            {"id": "impact", "status": "ready", "checks": [{"id": "recommendations-tests", "command": _ok_command()}]},
            {"id": "final", "status": "ready", "checks": [{"id": "repository-tests", "command": _ok_command()}]},
        ])
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            run_dir = workspace / ".esc-ai" / "runs" / "run-1"
            document = execute_verification_plan(plan, workspace, run_dir)
            self.assertEqual("passed", document["status"])
            outcomes = {gate["id"]: gate["outcome"] for gate in document["gates"]}
            self.assertEqual(
                {"focused": "skipped", "component": "completed", "impact": "completed", "final": "completed"},
                outcomes,
            )
            for gate in document["gates"][1:]:
                check = gate["checks"][0]
                self.assertEqual("passed", check["status"])
                self.assertEqual(0, check["exit_code"])
                self.assertIsInstance(check["duration_ms"], int)
                self.assertTrue((workspace / check["stdout_path"]).is_file())
            result = validate_contract("verification-result", run_dir / "verification-result.json")
            self.assertEqual(ManifestState.VALID, result.state, result.messages)

    def test_failure_stops_remaining_gates_and_checks(self):
        plan = _plan([
            {"id": "focused", "status": "not-applicable", "checks": []},
            {
                "id": "component",
                "status": "ready",
                "checks": [
                    {"id": "first", "command": _failing_command()},
                    {"id": "second", "command": _ok_command()},
                ],
            },
            {"id": "impact", "status": "ready", "checks": [{"id": "recommendations-tests", "command": _ok_command()}]},
            {"id": "final", "status": "ready", "checks": [{"id": "repository-tests", "command": _ok_command()}]},
        ])
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            run_dir = workspace / ".esc-ai" / "runs" / "run-2"
            document = execute_verification_plan(plan, workspace, run_dir)
            self.assertEqual("failed", document["status"])
            gates_by_id = {gate["id"]: gate for gate in document["gates"]}
            component_checks = {check["id"]: check for check in gates_by_id["component"]["checks"]}
            self.assertEqual("failed", component_checks["first"]["status"])
            self.assertEqual(1, component_checks["first"]["exit_code"])
            self.assertEqual("not-run", component_checks["second"]["status"])
            self.assertIsNone(component_checks["second"]["exit_code"])
            self.assertEqual("not-run", gates_by_id["impact"]["outcome"])
            self.assertEqual("not-run", gates_by_id["impact"]["checks"][0]["status"])
            self.assertEqual("not-run", gates_by_id["final"]["outcome"])
            result = validate_contract("verification-result", run_dir / "verification-result.json")
            self.assertEqual(ManifestState.VALID, result.state, result.messages)

    def test_input_required_gate_is_skipped_not_executed(self):
        plan = _plan([
            {
                "id": "focused",
                "status": "input-required",
                "checks": [{"id": "content-focused-tests", "command": ["./gradlew", "test", "--tests", "{test_filter}"], "requires": ["test_filter"]}],
            },
            {"id": "component", "status": "ready", "checks": [{"id": "content-tests", "command": _ok_command()}]},
            {"id": "impact", "status": "not-applicable", "checks": []},
            {"id": "final", "status": "ready", "checks": [{"id": "repository-tests", "command": _ok_command()}]},
        ])
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            run_dir = workspace / ".esc-ai" / "runs" / "run-3"
            document = execute_verification_plan(plan, workspace, run_dir)
            self.assertEqual("passed", document["status"])
            gates_by_id = {gate["id"]: gate for gate in document["gates"]}
            self.assertEqual("skipped", gates_by_id["focused"]["outcome"])
            self.assertEqual("skipped", gates_by_id["focused"]["checks"][0]["status"])
            self.assertIsNone(gates_by_id["focused"]["checks"][0]["exit_code"])
            result = validate_contract("verification-result", run_dir / "verification-result.json")
            self.assertEqual(ManifestState.VALID, result.state, result.messages)

    def test_missing_executable_surfaces_as_error_not_a_crash(self):
        plan = _plan([
            {"id": "focused", "status": "not-applicable", "checks": []},
            {"id": "component", "status": "ready", "checks": [{"id": "content-tests", "command": ["esc-ai-nonexistent-binary-xyz"]}]},
            {"id": "impact", "status": "not-applicable", "checks": []},
            {"id": "final", "status": "ready", "checks": [{"id": "repository-tests", "command": _ok_command()}]},
        ])
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            run_dir = workspace / ".esc-ai" / "runs" / "run-4"
            document = execute_verification_plan(plan, workspace, run_dir)
            self.assertEqual("failed", document["status"])
            gates_by_id = {gate["id"]: gate for gate in document["gates"]}
            check = gates_by_id["component"]["checks"][0]
            self.assertEqual("error", check["status"])
            self.assertIsNone(check["exit_code"])
            self.assertEqual("not-run", gates_by_id["final"]["outcome"])
            result = validate_contract("verification-result", run_dir / "verification-result.json")
            self.assertEqual(ManifestState.VALID, result.state, result.messages)

    def test_timeout_surfaces_as_error(self):
        plan = _plan([
            {"id": "focused", "status": "not-applicable", "checks": []},
            {
                "id": "component",
                "status": "ready",
                "checks": [{"id": "content-tests", "command": [sys.executable, "-c", "import time; time.sleep(5)"]}],
            },
            {"id": "impact", "status": "not-applicable", "checks": []},
            {"id": "final", "status": "not-applicable", "checks": []},
        ])
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            run_dir = workspace / ".esc-ai" / "runs" / "run-5"
            document = execute_verification_plan(plan, workspace, run_dir, timeout_seconds=1)
            self.assertEqual("failed", document["status"])
            gates_by_id = {gate["id"]: gate for gate in document["gates"]}
            check = gates_by_id["component"]["checks"][0]
            self.assertEqual("error", check["status"])
            self.assertIsNone(check["exit_code"])
            result = validate_contract("verification-result", run_dir / "verification-result.json")
            self.assertEqual(ManifestState.VALID, result.state, result.messages)

    def _write_report_profile(self, workspace: Path) -> None:
        (workspace / "esc-report-profile.yaml").write_text(
            "schema_version: 1\nprofile:\n  id: p\n  format: junit-xml\n"
            "limits:\n  max_failures: 10\n  max_message_chars: 200\n",
            encoding="utf-8",
        )

    def test_report_glob_match_is_summarized_and_referenced(self):
        plan = _plan([
            {"id": "focused", "status": "not-applicable", "checks": []},
            {
                "id": "component",
                "status": "ready",
                "checks": [{
                    "id": "content-tests",
                    "command": _ok_command(),
                    "report": {"glob": "reports/*.xml", "profile": "esc-report-profile.yaml"},
                }],
            },
            {"id": "impact", "status": "not-applicable", "checks": []},
            {"id": "final", "status": "not-applicable", "checks": []},
        ])
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            self._write_report_profile(workspace)
            reports_dir = workspace / "reports"
            reports_dir.mkdir()
            (reports_dir / "TEST-A.xml").write_text(
                '<testsuite><testcase classname="A" name="ok"/></testsuite>', encoding="utf-8",
            )
            run_dir = workspace / ".esc-ai" / "runs" / "run-report-1"
            document = execute_verification_plan(plan, workspace, run_dir)
            check = document["gates"][1]["checks"][0]
            self.assertEqual("passed", check["status"])
            self.assertIsNotNone(check["report_path"])
            self.assertTrue((workspace / check["report_path"]).is_file())
            result = validate_contract("verification-result", run_dir / "verification-result.json")
            self.assertEqual(ManifestState.VALID, result.state, result.messages)

    def test_report_glob_aggregates_multiple_matches(self):
        plan = _plan([
            {"id": "focused", "status": "not-applicable", "checks": []},
            {
                "id": "component",
                "status": "ready",
                "checks": [{
                    "id": "content-tests",
                    "command": _ok_command(),
                    "report": {"glob": "reports/*.xml", "profile": "esc-report-profile.yaml"},
                }],
            },
            {"id": "impact", "status": "not-applicable", "checks": []},
            {"id": "final", "status": "not-applicable", "checks": []},
        ])
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            self._write_report_profile(workspace)
            reports_dir = workspace / "reports"
            reports_dir.mkdir()
            (reports_dir / "TEST-A.xml").write_text(
                '<testsuite><testcase classname="A" name="ok"/></testsuite>', encoding="utf-8",
            )
            (reports_dir / "TEST-B.xml").write_text(
                '<testsuite><testcase classname="B" name="broke"><failure message="x"/></testcase></testsuite>',
                encoding="utf-8",
            )
            run_dir = workspace / ".esc-ai" / "runs" / "run-report-2"
            document = execute_verification_plan(plan, workspace, run_dir)
            check = document["gates"][1]["checks"][0]
            import json
            summary = json.loads((workspace / check["report_path"]).read_text(encoding="utf-8"))
            self.assertEqual(2, summary["totals"]["tests"])
            self.assertEqual(1, summary["totals"]["failed"])

    def test_report_glob_with_no_matches_leaves_report_path_null(self):
        plan = _plan([
            {"id": "focused", "status": "not-applicable", "checks": []},
            {
                "id": "component",
                "status": "ready",
                "checks": [{
                    "id": "content-tests",
                    "command": _ok_command(),
                    "report": {"glob": "reports/*.xml", "profile": "esc-report-profile.yaml"},
                }],
            },
            {"id": "impact", "status": "not-applicable", "checks": []},
            {"id": "final", "status": "not-applicable", "checks": []},
        ])
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            self._write_report_profile(workspace)
            run_dir = workspace / ".esc-ai" / "runs" / "run-report-3"
            document = execute_verification_plan(plan, workspace, run_dir)
            check = document["gates"][1]["checks"][0]
            self.assertEqual("passed", check["status"])
            self.assertIsNone(check["report_path"])

    def test_malformed_report_does_not_crash_run(self):
        plan = _plan([
            {"id": "focused", "status": "not-applicable", "checks": []},
            {
                "id": "component",
                "status": "ready",
                "checks": [{
                    "id": "content-tests",
                    "command": _ok_command(),
                    "report": {"glob": "reports/*.xml", "profile": "esc-report-profile.yaml"},
                }],
            },
            {"id": "impact", "status": "not-applicable", "checks": []},
            {"id": "final", "status": "not-applicable", "checks": []},
        ])
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            self._write_report_profile(workspace)
            reports_dir = workspace / "reports"
            reports_dir.mkdir()
            (reports_dir / "TEST-A.xml").write_text("not valid xml <<<", encoding="utf-8")
            run_dir = workspace / ".esc-ai" / "runs" / "run-report-4"
            document = execute_verification_plan(plan, workspace, run_dir)
            check = document["gates"][1]["checks"][0]
            self.assertEqual("passed", check["status"])
            self.assertIsNone(check["report_path"])
            result = validate_contract("verification-result", run_dir / "verification-result.json")
            self.assertEqual(ManifestState.VALID, result.state, result.messages)

    def test_report_not_located_for_not_run_or_skipped_checks(self):
        plan = _plan([
            {
                "id": "focused",
                "status": "input-required",
                "checks": [{
                    "id": "x", "command": ["./gradlew", "test", "--tests", "{f}"], "requires": ["f"],
                    "report": {"glob": "reports/*.xml", "profile": "esc-report-profile.yaml"},
                }],
            },
            {
                "id": "component",
                "status": "ready",
                "checks": [{
                    "id": "first", "command": _failing_command(),
                    "report": {"glob": "reports/*.xml", "profile": "esc-report-profile.yaml"},
                }, {
                    "id": "second", "command": _ok_command(),
                    "report": {"glob": "reports/*.xml", "profile": "esc-report-profile.yaml"},
                }],
            },
            {"id": "impact", "status": "not-applicable", "checks": []},
            {"id": "final", "status": "not-applicable", "checks": []},
        ])
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            self._write_report_profile(workspace)
            reports_dir = workspace / "reports"
            reports_dir.mkdir()
            (reports_dir / "TEST-A.xml").write_text(
                '<testsuite><testcase classname="A" name="ok"/></testsuite>', encoding="utf-8",
            )
            run_dir = workspace / ".esc-ai" / "runs" / "run-report-5"
            document = execute_verification_plan(plan, workspace, run_dir)
            focused_check = document["gates"][0]["checks"][0]
            self.assertEqual("skipped", focused_check["status"])
            self.assertIsNone(focused_check["report_path"])
            component_checks = {c["id"]: c for c in document["gates"][1]["checks"]}
            self.assertEqual("failed", component_checks["first"]["status"])
            self.assertIsNotNone(component_checks["first"]["report_path"])
            self.assertEqual("not-run", component_checks["second"]["status"])
            self.assertIsNone(component_checks["second"]["report_path"])

    def test_run_dir_outside_workspace_root_is_rejected(self):
        plan = _plan([
            {"id": "focused", "status": "not-applicable", "checks": []},
            {"id": "component", "status": "ready", "checks": [{"id": "content-tests", "command": _ok_command()}]},
            {"id": "impact", "status": "not-applicable", "checks": []},
            {"id": "final", "status": "not-applicable", "checks": []},
        ])
        with TemporaryDirectory() as workspace_temp, TemporaryDirectory() as run_temp:
            with self.assertRaises(ValueError):
                execute_verification_plan(plan, Path(workspace_temp), Path(run_temp))


if __name__ == "__main__":
    unittest.main()
