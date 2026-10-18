from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.lint_config import COVERAGE, generate_eslint_config, load_limits, write_eslint_config
from esc_exec.yaml_io import write_yaml

LIMITS = {"maxModuleLines": 300, "maxFunctionLines": 50, "maxComplexity": 10}


class GenerateTests(unittest.TestCase):
    def test_uses_the_declared_limits_and_names_the_rule_each_lint_rule_serves(self):
        config = generate_eslint_config(LIMITS)
        self.assertIn("max: 300", config)
        self.assertIn("max: 50", config)
        self.assertIn("complexity: ['error', 10]", config)
        for rule in ("ARCH-WEB-APP-MOD-SIZE-01", "ARCH-WEB-APP-DOM-CLOCK-01", "PLAT-WEB-RP-DEPS-01"):
            self.assertIn(rule, config)

    def test_a_component_limit_adds_its_own_block_only_when_declared(self):
        self.assertNotIn("components/**", generate_eslint_config(LIMITS))
        config = generate_eslint_config({**LIMITS, "maxComponentFunctionLines": 150})
        self.assertIn("src/**/components/**/*.tsx", config)
        self.assertIn("max: 150", config)

    def test_unknown_template_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "unknown lint template"):
            generate_eslint_config(LIMITS, "nope")

    def test_every_covered_rule_says_how_much_of_it_a_linter_checks(self):
        self.assertTrue(all(kind in {"full", "partial", "architecture-check"} for kind, _ in COVERAGE.values()))
        self.assertEqual("partial", COVERAGE["ARCH-WEB-APP-EFLOW-ASSERT-01"][0])


class LimitsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_missing_limits_file_fails_instead_of_defaulting(self):
        with self.assertRaisesRegex(ValueError, "sets no default sizes"):
            load_limits(self.root)

    def test_a_missing_or_invalid_limit_names_the_rule_that_needs_it(self):
        (self.root / ".esc-ai").mkdir()
        write_yaml(self.root / ".esc-ai/lint-limits.yaml", {"maxModuleLines": 300, "maxFunctionLines": 50})
        with self.assertRaisesRegex(ValueError, "maxComplexity.*MOD-FUNC-01"):
            load_limits(self.root)
        write_yaml(self.root / ".esc-ai/lint-limits.yaml", {**LIMITS, "maxModuleLines": 0})
        with self.assertRaisesRegex(ValueError, "maxModuleLines"):
            load_limits(self.root)

    def test_reads_valid_limits(self):
        (self.root / ".esc-ai").mkdir()
        write_yaml(self.root / ".esc-ai/lint-limits.yaml", LIMITS)
        self.assertEqual(300, load_limits(self.root)["maxModuleLines"])


if __name__ == "__main__":
    unittest.main()
