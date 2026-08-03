import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.contracts import validate_contract
from esc_exec.cli import main
from esc_exec.model import ManifestState
from esc_exec.reporting import summarize_coverage_report, summarize_junit, summarize_junit_reports


# Mirrors the real, verified-live structure of both Kover's and JaCoCo's XML
# reports (see plan/done/coverage-threshold-enforcement.md's "What we found") --
# a <report> root with <package> detail followed by report-level, direct-child
# <counter> totals.
_COVERAGE_XML = (
    '<?xml version="1.0"?>'
    '<report name="sample">'
    '<package name="com/example">'
    '<class name="com/example/Thing" sourcefilename="Thing.kt">'
    '<counter type="LINE" missed="2" covered="8"/>'
    '</class>'
    '</package>'
    '<counter type="INSTRUCTION" missed="10" covered="90"/>'
    '<counter type="BRANCH" missed="1" covered="9"/>'
    '<counter type="LINE" missed="20" covered="80"/>'
    '</report>'
)


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

    def test_summarize_junit_reports_aggregates_multiple_files(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "TEST-A.xml"
            first.write_text(
                '<testsuite><testcase classname="A" name="ok" time="0.1"/>'
                '<testcase classname="A" name="broken"><failure message="a broke"/></testcase></testsuite>',
                encoding="utf-8",
            )
            second = root / "TEST-B.xml"
            second.write_text(
                '<testsuite><testcase classname="B" name="ok" time="0.2"/>'
                '<testcase classname="B" name="also-broken"><error>b errored</error></testcase></testsuite>',
                encoding="utf-8",
            )
            profile = root / "profile.yaml"
            profile.write_text(
                "schema_version: 1\nprofile:\n  id: multi\n  format: junit-xml\n"
                "limits:\n  max_failures: 10\n  max_message_chars: 100\n",
                encoding="utf-8",
            )
            output = root / "verification-summary.json"
            result = summarize_junit_reports([first, second], profile, output, "reports/dir")

            self.assertEqual("error", result["verification"]["status"])
            self.assertEqual(
                {"tests": 4, "passed": 2, "failed": 1, "errors": 1, "skipped": 0, "duration_ms": 300},
                result["totals"],
            )
            self.assertEqual(2, len(result["failures"]))
            self.assertEqual({"a broke", "b errored"}, {failure["message"] for failure in result["failures"]})
            self.assertEqual("reports/dir", result["full_report"]["path"])
            self.assertEqual(ManifestState.VALID, validate_contract("verification-summary", output).state)

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


class CoverageReportingTests(unittest.TestCase):
    """
    plan/done/coverage-threshold-enforcement.md: one parser handles both Kover's
    and JaCoCo's XML output, verified live against real reports from both tools
    in the two grounding repositories -- see _COVERAGE_XML's own comment.
    """

    def _write(self, root: Path, counter_type: str = "LINE", threshold=None) -> tuple[Path, Path]:
        source = root / "coverage.xml"
        source.write_text(_COVERAGE_XML, encoding="utf-8")
        profile = root / "profile.yaml"
        limits = f"  counter_type: {counter_type}\n" + (f"  threshold: {threshold}\n" if threshold is not None else "")
        profile.write_text(
            f"schema_version: 1\nprofile:\n  id: p\n  format: coverage-xml\nlimits:\n{limits}",
            encoding="utf-8",
        )
        return source, profile

    def test_defaults_to_line_counter_and_is_report_only_without_a_threshold(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source, profile = self._write(root)
            output = root / "coverage-summary.json"
            result = summarize_coverage_report(source, profile, output, "reports/coverage.xml")
            self.assertEqual("passed", result["coverage"]["status"])
            self.assertEqual({"counter_type": "LINE", "missed": 20, "covered": 80, "percent": 80.0}, result["totals"])
            self.assertEqual({"required": None, "met": True}, result["threshold"])
            self.assertEqual("reports/coverage.xml", result["full_report"]["path"])
            self.assertEqual(ManifestState.VALID, validate_contract("coverage-summary", output).state)

    def test_meeting_the_threshold_passes(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source, profile = self._write(root, threshold=80)
            output = root / "coverage-summary.json"
            result = summarize_coverage_report(source, profile, output)
            self.assertEqual("passed", result["coverage"]["status"])
            self.assertTrue(result["threshold"]["met"])

    def test_missing_the_threshold_fails(self):
        """The real enforcement: the report-generation command exits 0
        regardless of coverage level -- this is what turns that into a real
        pass/fail decision."""
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source, profile = self._write(root, threshold=90)
            output = root / "coverage-summary.json"
            result = summarize_coverage_report(source, profile, output)
            self.assertEqual("failed", result["coverage"]["status"])
            self.assertFalse(result["threshold"]["met"])

    def test_selects_the_declared_counter_type(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source, profile = self._write(root, counter_type="INSTRUCTION")
            output = root / "coverage-summary.json"
            result = summarize_coverage_report(source, profile, output)
            self.assertEqual({"counter_type": "INSTRUCTION", "missed": 10, "covered": 90, "percent": 90.0}, result["totals"])

    def test_invalid_counter_type_is_rejected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source, profile = self._write(root, counter_type="BOGUS")
            with self.assertRaisesRegex(ValueError, "counter_type"):
                summarize_coverage_report(source, profile, root / "out.json")

    def test_missing_report_level_counter_is_rejected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source, profile = self._write(root, counter_type="CLASS")  # not present in the fixture
            with self.assertRaisesRegex(ValueError, "no report-level counter"):
                summarize_coverage_report(source, profile, root / "out.json")

    def test_wrong_report_format_is_rejected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            profile = root / "profile.yaml"
            profile.write_text(
                "schema_version: 1\nprofile:\n  id: p\n  format: junit-xml\nlimits: {}\n", encoding="utf-8",
            )
            source = root / "coverage.xml"
            source.write_text(_COVERAGE_XML, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "coverage-xml"):
                summarize_coverage_report(source, profile, root / "out.json")

    def test_wrong_xml_root_is_rejected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            _, profile = self._write(root)
            source = root / "not-coverage.xml"
            source.write_text("<testsuite></testsuite>", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "coverage XML root"):
                summarize_coverage_report(source, profile, root / "out.json")
