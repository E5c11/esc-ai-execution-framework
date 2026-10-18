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

    def test_a_project_without_src_lints_its_own_source_folders_and_nothing_else(self):
        config = generate_eslint_config({**LIMITS, "maxComponentFunctionLines": 150}, base="")
        for folder in ("app", "features", "shared", "composition", "data"):
            self.assertIn(f"'{folder}/**/*.{{ts,tsx}}'", config)
        self.assertNotIn("src/", config)
        self.assertIn("'features/**/domain/**/*.ts'", config)
        self.assertIn("'features/**/components/**/*.tsx'", config)
        self.assertIn("'features/**/index.ts'", config)
        self.assertNotIn("'**/*.{ts,tsx}'", config)

    def test_unknown_template_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "unknown lint template"):
            generate_eslint_config(LIMITS, "nope")

    def test_every_covered_rule_says_how_much_of_it_a_linter_checks(self):
        self.assertTrue(all(kind in {"full", "partial", "architecture-check"} for kind, _ in COVERAGE.values()))
        self.assertEqual("partial", COVERAGE["ARCH-WEB-APP-EFLOW-ASSERT-01"][0])


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / ".esc-ai").mkdir()
        write_yaml(self.root / ".esc-ai/lint-limits.yaml", LIMITS)
        (self.root / "features/faults").mkdir(parents=True)
        (self.root / "eslint.config.mjs").write_text("export default []\n", encoding="utf-8")
        write_yaml(self.root / ".esc-ai/esc-index.json", {})
        import json
        (self.root / ".esc-ai/esc-index.json").write_text(json.dumps({"components": [{"id": "web", "path": "."}]}), encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_detects_a_project_without_src_and_writes_beside_an_existing_config_when_asked(self):
        output = write_eslint_config(self.root, "web", output="eslint.architecture.config.ts")
        self.assertEqual("eslint.architecture.config.ts", output.name)
        self.assertIn("'features/**/*.{ts,tsx}'", output.read_text(encoding="utf-8"))
        self.assertEqual("export default []\n", (self.root / "eslint.config.mjs").read_text(encoding="utf-8"))

    def test_keeps_refusing_to_overwrite_without_force(self):
        write_eslint_config(self.root, "web", output="eslint.architecture.config.ts")
        with self.assertRaisesRegex(ValueError, "already exists"):
            write_eslint_config(self.root, "web", output="eslint.architecture.config.ts")

    def test_the_output_must_stay_inside_the_component(self):
        with self.assertRaisesRegex(ValueError, "inside the component"):
            write_eslint_config(self.root, "web", output="../elsewhere.ts")


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
