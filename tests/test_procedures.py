import unittest

from esc_exec.procedures import IMPLEMENT, PROCEDURE_WORK_TYPES, PROCEDURES, VERIFY, procedure_for


class ProceduresTests(unittest.TestCase):
    def test_every_stage_is_mandatory_in_v1(self) -> None:
        for work_type, stages in PROCEDURES.items():
            for stage in stages:
                self.assertTrue(stage.mandatory, f"{work_type}/{stage.name} must be mandatory in v1")

    def test_read_only_work_types_exclude_implement(self) -> None:
        for work_type in ("investigate", "plan"):
            names = [stage.name for stage in PROCEDURES[work_type]]
            self.assertNotIn("implement", names, f"{work_type} must not include implement")

    def test_verify_present_and_never_variable_where_it_appears(self) -> None:
        for work_type, stages in PROCEDURES.items():
            if IMPLEMENT in stages:
                self.assertIn(VERIFY, stages, f"{work_type} implements changes but has no verify stage")
        self.assertEqual(VERIFY.interaction, "fixed")

    def test_architecture_gate_is_variable_but_present_where_declared(self) -> None:
        for work_type in ("investigate", "plan", "fix", "refactor", "feature", "job"):
            names = [stage.name for stage in PROCEDURES[work_type]]
            self.assertIn("architecture_gate", names, f"{work_type} must include architecture_gate")

    def test_fix_captures_root_cause_before_implementation(self) -> None:
        names = [stage.name for stage in PROCEDURES["fix"]]
        self.assertIn("root_cause", names)
        self.assertLess(names.index("root_cause"), names.index("implement"))

    def test_refactor_captures_baseline_before_implementation(self) -> None:
        names = [stage.name for stage in PROCEDURES["refactor"]]
        self.assertIn("baseline_capture", names)
        self.assertLess(names.index("baseline_capture"), names.index("implement"))

    def test_document_has_no_architecture_gate_but_has_grounding_check(self) -> None:
        names = [stage.name for stage in PROCEDURES["document"]]
        self.assertNotIn("architecture_gate", names)
        self.assertIn("grounding_check", names)

    def test_job_and_feature_run_the_same_gates(self) -> None:
        job_names = [stage.name for stage in PROCEDURES["job"]]
        feature_names = [stage.name for stage in PROCEDURES["feature"]]
        self.assertEqual(job_names, feature_names)

    def test_investigate_and_plan_share_the_same_read_only_shape(self) -> None:
        self.assertEqual(PROCEDURES["investigate"], PROCEDURES["plan"])

    def test_procedure_for_rejects_unknown_work_type(self) -> None:
        with self.assertRaises(ValueError):
            procedure_for("not-a-real-work-type")

    def test_procedure_for_returns_the_declared_procedure(self) -> None:
        self.assertEqual(procedure_for("fix"), PROCEDURES["fix"])

    def test_procedure_work_types_matches_procedures_keys(self) -> None:
        self.assertEqual(set(PROCEDURE_WORK_TYPES), set(PROCEDURES.keys()))


if __name__ == "__main__":
    unittest.main()
