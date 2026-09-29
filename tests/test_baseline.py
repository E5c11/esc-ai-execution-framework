"""The `baseline_capture` stage of the refactor procedure."""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from esc_exec.baseline import baseline_blockers, passed_check_count, plan_blockers
from esc_exec.procedures import BASELINE_CAPTURE
from esc_exec.verification_execution import execute_verification_plan


def gate(gate_id, status, *checks):
    return {"id": gate_id, "status": status, "checks": [{"id": c, "command": [sys.executable, "-c", "pass"]} for c in checks]}


def plan(*gates):
    return {
        "schema_version": 1, "task_id": "t", "profiles": [],
        "strategy": {"order": ["focused", "component", "impact", "final"], "stop_on_failure": True},
        "impact": {"graph": "g.json", "source_components": [], "consumer_components": []},
        "gates": list(gates),
    }


def result(**check_statuses):
    return {"gates": [{"id": "final", "outcome": "completed", "checks": [{"id": name, "status": status} for name, status in check_statuses.items()]}]}


class PlanBlockerTests(unittest.TestCase):
    def test_a_plan_with_a_runnable_check_is_accepted(self):
        self.assertEqual([], plan_blockers(plan(gate("final", "ready", "unit"))))

    def test_a_plan_where_nothing_would_run_is_refused(self):
        cases = {
            "no checks anywhere": plan(gate("focused", "not-applicable"), gate("final", "ready")),
            "checks only in a gate awaiting input": plan(gate("focused", "input-required", "focused-tests"), gate("final", "not-applicable")),
            "no gates": plan(),
        }
        for name, candidate in cases.items():
            with self.subTest(case=name):
                self.assertEqual(1, len(plan_blockers(candidate)))
                self.assertIn("nothing could show a refactor preserved behaviour", plan_blockers(candidate)[0])

    def test_this_is_the_vacuous_pass_the_stage_exists_to_prevent(self):
        """Verification itself reports `passed` for a plan in which nothing ran."""
        empty = plan(gate("focused", "not-applicable"), gate("component", "not-applicable"), gate("impact", "not-applicable"), gate("final", "not-applicable"))
        with TemporaryDirectory() as temp:
            outcome = execute_verification_plan(empty, Path(temp), Path(temp) / ".esc-ai" / "runs" / "r")
        self.assertEqual("passed", outcome["status"])
        self.assertEqual(0, passed_check_count(outcome))
        self.assertEqual(1, len(plan_blockers(empty)))
        self.assertEqual(1, len(baseline_blockers(outcome)))


class BaselineBlockerTests(unittest.TestCase):
    def test_a_green_baseline_is_accepted(self):
        self.assertEqual([], baseline_blockers(result(unit="passed", lint="passed")))

    def test_each_check_that_is_not_green_is_named(self):
        blockers = baseline_blockers(result(unit="failed", lint="error", fine="passed"))
        self.assertEqual(2, len(blockers))
        self.assertTrue(any("final.unit is failed" in blocker for blocker in blockers))
        self.assertTrue(any("final.lint is error" in blocker for blocker in blockers))
        self.assertTrue(all("escape-ai fix" in blocker for blocker in blockers))

    def test_a_baseline_where_nothing_passed_is_refused_even_though_nothing_failed(self):
        for statuses in ({}, {"unit": "skipped"}, {"unit": "not-run"}):
            with self.subTest(statuses=statuses):
                self.assertEqual(["no verification check actually ran against the untouched code, so there is no baseline"], baseline_blockers(result(**statuses)))

    def test_skipped_checks_do_not_block_a_baseline_that_otherwise_passed(self):
        self.assertEqual([], baseline_blockers(result(unit="passed", extra="skipped")))

    def test_the_passed_count_counts_only_passed_checks(self):
        self.assertEqual(2, passed_check_count(result(a="passed", b="passed", c="skipped", d="failed")))


class ProcedureClaimTests(unittest.TestCase):
    def test_baseline_capture_is_no_longer_declared_new_and_names_its_enforcement(self):
        self.assertFalse(BASELINE_CAPTURE.maps_to.startswith("new"))
        self.assertIn("esc_exec.baseline", BASELINE_CAPTURE.maps_to)
        self.assertIn("vacuously", BASELINE_CAPTURE.maps_to)

    def test_it_is_a_gate_because_it_can_refuse_a_run(self):
        self.assertEqual("gate", BASELINE_CAPTURE.kind)
        self.assertTrue(BASELINE_CAPTURE.mandatory)


if __name__ == "__main__":
    unittest.main()
