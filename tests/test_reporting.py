import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.contracts import validate_contract
from esc_exec.cli import main
from esc_exec.model import ManifestState
from esc_exec.reporting import summarize_junit


class ReportingTests(unittest.TestCase):
    def test_junit_summary_is_bounded_and_retains_report_reference(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "junit.xml"
            source.write_text(
                '<testsuite><testcase classname="A" name="ok" time="0.1"/>'
                '<testcase classname="A" name="one"><failure message="first failure with detail"/></testcase>'
                '<testcase classname="B" name="two"><error>second error with detail</error></testcase>'
                '<testcase name="skip"><skipped/></testcase></testsuite>',
                encoding="utf-8",
            )
            profile = root / "profile.yaml"
            profile.write_text(
                "schema_version: 1\nprofile:\n  id: small\n  format: junit-xml\n"
                "limits:\n  max_failures: 1\n  max_message_chars: 8\n",
                encoding="utf-8",
            )
            output = root / "verification-summary.json"
            result = summarize_junit(source, profile, output, "reports/junit.xml")

            self.assertEqual("error", result["verification"]["status"])
            self.assertEqual(
                {"tests": 4, "passed": 1, "failed": 1, "errors": 1, "skipped": 1, "duration_ms": 100},
                result["totals"],
            )
            self.assertEqual(1, len(result["failures"]))
            self.assertEqual("first fa", result["failures"][0]["message"])
            self.assertEqual(1, result["truncation"]["failures_omitted"])
            self.assertEqual("reports/junit.xml", result["full_report"]["path"])
            self.assertEqual(ManifestState.VALID, validate_contract("verification-summary", output).state)

    def test_invalid_summary_totals_are_rejected(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "summary.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "verification": {"profile": "p", "source_format": "junit-xml", "status": "passed", "generated_at": "now"},
                "totals": {"tests": 1, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "duration_ms": 0},
                "failures": [],
                "truncation": {"failures_included": 0, "failures_omitted": 0, "max_failures": 1, "max_message_chars": 10},
                "full_report": {"path": "junit.xml", "media_type": "application/xml"},
            }), encoding="utf-8")
            self.assertEqual(ManifestState.INVALID, validate_contract("verification-summary", path).state)

    def test_report_cli_writes_summary(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "junit.xml"
            source.write_text('<testsuite><testcase name="ok"/></testsuite>', encoding="utf-8")
            profile = root / "profile.yaml"
            profile.write_text(
                "schema_version: 1\nprofile:\n  id: cli\n  format: junit-xml\n"
                "limits:\n  max_failures: 2\n  max_message_chars: 20\n",
                encoding="utf-8",
            )
            output = root / "verification-summary.json"
            with redirect_stdout(StringIO()) as stdout:
                exit_code = main(["report", "summarize", str(profile), str(source), str(output)])
            self.assertEqual(0, exit_code)
            self.assertIn("PASSED", stdout.getvalue())
            self.assertTrue(output.is_file())
