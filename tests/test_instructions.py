import unittest

from esc_exec.instructions import check_extension_namespace_conflict, order_instruction_bundle


class InstructionPrecedenceTests(unittest.TestCase):
    def test_orders_populated_levels_and_omits_empty_ones(self):
        bundle = {
            "component_manifests_and_profiles": ["content/esc-component.yaml"],
            "safety_and_operator_policy": ["policy.yaml"],
            "architecture_framework_core_and_profile": ["CORE-DI", "ARCH-BE"],
        }
        ordered = order_instruction_bundle(bundle)
        self.assertEqual(
            ["safety_and_operator_policy", "architecture_framework_core_and_profile", "component_manifests_and_profiles"],
            [entry["level"] for entry in ordered],
        )
        self.assertEqual(["CORE-DI", "ARCH-BE"], ordered[1]["sources"])

    def test_unknown_level_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown instruction precedence level"):
            order_instruction_bundle({"not_a_real_level": ["x"]})

    def test_empty_bundle_orders_to_nothing(self):
        self.assertEqual([], order_instruction_bundle({}))

    def test_project_specific_prefix_is_not_a_conflict(self):
        self.assertEqual([], check_extension_namespace_conflict(["AMPM-BE-AUTH-01", "AMPM-MOBILE-NOTIF-02"]))

    def test_reserved_prefix_in_extension_is_a_conflict(self):
        self.assertEqual(
            ["CORE-DI", "ORCH-BE-FEAT"],
            check_extension_namespace_conflict(["CORE-DI", "AMPM-BE-AUTH-01", "ORCH-BE-FEAT"]),
        )

    def test_no_extension_ids_is_no_conflict(self):
        self.assertEqual([], check_extension_namespace_conflict([]))


if __name__ == "__main__":
    unittest.main()
