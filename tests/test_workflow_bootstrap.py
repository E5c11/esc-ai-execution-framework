from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.workflow_bootstrap import (
    bootstrap_workflow_inheritance,
    parse_workflow_policy_frontmatter,
    validate_workflow_policy,
)


class WorkflowBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manifest = {"repository": {"id": "sample"}}

    def tearDown(self):
        self.temp.cleanup()

    def test_fresh_repository_creates_all_four_files(self):
        result = bootstrap_workflow_inheritance(self.root, self.manifest)
        self.assertEqual(
            {
                "INSTRUCTIONS.md",
                ".esc-ai/workflows/README.md",
                ".esc-ai/workflows/active/README.md",
                ".esc-ai/workflows/archive/README.md",
            },
            set(result["created"]),
        )
        self.assertEqual([], result["existing"])
        self.assertEqual([], result["advisory_warnings"])
        self.assertTrue((self.root / "INSTRUCTIONS.md").is_file())
        self.assertTrue((self.root / ".esc-ai/workflows/README.md").is_file())
        self.assertIn("esc-ai-execution-framework", (self.root / "INSTRUCTIONS.md").read_text())
        self.assertIn("esc-ai-architecture-framework", (self.root / "INSTRUCTIONS.md").read_text())

    def test_generated_workflow_readme_frontmatter_is_valid(self):
        bootstrap_workflow_inheritance(self.root, self.manifest)
        text = (self.root / ".esc-ai/workflows/README.md").read_text()
        frontmatter = parse_workflow_policy_frontmatter(text)
        self.assertEqual([], validate_workflow_policy(frontmatter))

    def test_rerunning_bootstrap_is_a_true_no_op(self):
        bootstrap_workflow_inheritance(self.root, self.manifest)
        instructions_path = self.root / "INSTRUCTIONS.md"
        original = instructions_path.read_text()
        original_mtime = instructions_path.stat().st_mtime_ns

        result = bootstrap_workflow_inheritance(self.root, self.manifest)

        self.assertEqual([], result["created"])
        self.assertEqual(
            {
                "INSTRUCTIONS.md",
                ".esc-ai/workflows/README.md",
                ".esc-ai/workflows/active/README.md",
                ".esc-ai/workflows/archive/README.md",
            },
            set(result["existing"]),
        )
        self.assertEqual(original, instructions_path.read_text())
        self.assertEqual(original_mtime, instructions_path.stat().st_mtime_ns)

    def test_preexisting_handwritten_workflow_readme_is_never_overwritten(self):
        workflow_readme = self.root / ".esc-ai/workflows/README.md"
        workflow_readme.parent.mkdir(parents=True)
        handwritten = "# This repo has a mature, hand-written workflow policy already.\n"
        workflow_readme.write_text(handwritten, encoding="utf-8")

        result = bootstrap_workflow_inheritance(self.root, self.manifest)

        self.assertIn(".esc-ai/workflows/README.md", result["existing"])
        self.assertNotIn(".esc-ai/workflows/README.md", result["created"])
        self.assertEqual(handwritten, workflow_readme.read_text())

    def test_malformed_policy_frontmatter_is_rejected(self):
        messages = validate_workflow_policy({
            "schema_version": 1,
            "policy": {"extension": {"id": "x", "unexpected": "y"}, "unexpected_top_level": True},
        })
        self.assertTrue(any("unknown policy.extension field" in message for message in messages))

    def test_well_formed_policy_with_all_fields_is_valid(self):
        messages = validate_workflow_policy({
            "schema_version": 1,
            "policy": {
                "extension": {"id": "sample-backend-framework", "precedence": "overrides generic backend rules"},
                "final_gates": ["./gradlew check"],
                "commit_conventions": "Conventional Commits",
            },
        })
        self.assertEqual([], messages)


if __name__ == "__main__":
    unittest.main()
