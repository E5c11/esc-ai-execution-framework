from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.roadmap import (
    conversation_summary_path, load_conversation_summary, load_project_roadmap,
    project_roadmap_path, roadmap_prompt_line, save_conversation_summary, save_project_roadmap,
)


class ProjectRoadmapTests(unittest.TestCase):
    def test_no_roadmap_yet_returns_none(self):
        with TemporaryDirectory() as temp:
            self.assertIsNone(load_project_roadmap(Path(temp)))

    def test_save_and_load_round_trips(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            path = save_project_roadmap(
                root, "repo", "A demo app.", "Scaffolding initial structure.", "Add auth next.",
                durable_decisions=["Use Kotlin Multiplatform."],
            )
            self.assertEqual(root / ".esc-ai" / "roadmap.yaml", path)
            loaded = load_project_roadmap(root)
            roadmap = loaded["project_roadmap"]
            self.assertEqual("repo", roadmap["repository"])
            self.assertEqual("A demo app.", roadmap["purpose"])
            self.assertEqual("Scaffolding initial structure.", roadmap["current_stage"])
            self.assertEqual("Add auth next.", roadmap["direction"])
            self.assertEqual(["Use Kotlin Multiplatform."], roadmap["durable_decisions"])

    def test_missing_durable_decisions_defaults_to_empty_list(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            save_project_roadmap(root, "repo", "purpose", "stage", "direction")
            roadmap = load_project_roadmap(root)["project_roadmap"]
            self.assertEqual([], roadmap["durable_decisions"])

    def test_second_save_fully_overwrites_not_appends(self):
        # project_roadmap is updated in place, never a log -- confirmed by checking
        # that an old durable_decisions entry is gone unless the caller carries it
        # forward themselves.
        with TemporaryDirectory() as temp:
            root = Path(temp)
            save_project_roadmap(root, "repo", "purpose", "stage 1", "direction", durable_decisions=["Decision A."])
            save_project_roadmap(root, "repo", "purpose", "stage 2", "direction", durable_decisions=["Decision B."])
            roadmap = load_project_roadmap(root)["project_roadmap"]
            self.assertEqual("stage 2", roadmap["current_stage"])
            self.assertEqual(["Decision B."], roadmap["durable_decisions"])

    def test_path_helper_matches_save_location(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(root / ".esc-ai" / "roadmap.yaml", project_roadmap_path(root))


class RoadmapPromptLineTests(unittest.TestCase):
    """plan/done/project-vision-and-direction.md design 2: the substantive fix --
    a repository's roadmap actually reaching an adapter's real execution prompt,
    not just the next planning conversation's."""

    def test_no_roadmap_yet_returns_none(self):
        with TemporaryDirectory() as temp:
            self.assertIsNone(roadmap_prompt_line(Path(temp)))

    def test_renders_every_populated_field(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            save_project_roadmap(
                root, "repo", "A demo app.", "Scaffolding initial structure.", "Add auth next.",
                durable_decisions=["Use Kotlin Multiplatform."],
            )
            line = roadmap_prompt_line(root)
            self.assertIn("purpose: A demo app.", line)
            self.assertIn("current stage: Scaffolding initial structure.", line)
            self.assertIn("direction: Add auth next.", line)
            self.assertIn("durable decisions: Use Kotlin Multiplatform.", line)

    def test_omits_empty_fields_rather_than_rendering_them_blank(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            save_project_roadmap(root, "repo", "", "", "Add auth next.")
            line = roadmap_prompt_line(root)
            self.assertIn("direction: Add auth next.", line)
            self.assertNotIn("purpose:", line)
            self.assertNotIn("current stage:", line)
            self.assertNotIn("durable decisions:", line)

    def test_all_fields_empty_returns_none(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            save_project_roadmap(root, "repo", "", "", "")
            self.assertIsNone(roadmap_prompt_line(root))


class ConversationSummaryTests(unittest.TestCase):
    def test_no_summary_yet_returns_none(self):
        with TemporaryDirectory() as temp:
            self.assertIsNone(load_conversation_summary(Path(temp), "conv-1"))

    def test_save_and_load_round_trips(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            path = save_conversation_summary(
                root, "conv-1", "Plan feature X.", status="in-progress",
                completed=["Discussed scope."], decisions=["Use REST, not GraphQL."],
                remaining=["Confirm rollout plan."], open_questions=["Which repos are affected?"],
            )
            self.assertEqual(root / ".esc-ai" / "conversations" / "conv-1" / "summary.yaml", path)
            loaded = load_conversation_summary(root, "conv-1")
            self.assertEqual("conv-1", loaded["conversation_summary"]["id"])
            self.assertEqual("Plan feature X.", loaded["conversation_summary"]["purpose"])
            self.assertEqual("in-progress", loaded["conversation_summary"]["status"])
            self.assertEqual(["Discussed scope."], loaded["progress"]["completed"])
            self.assertEqual(["Use REST, not GraphQL."], loaded["progress"]["decisions"])
            self.assertEqual(["Confirm rollout plan."], loaded["progress"]["remaining"])
            self.assertEqual(["Which repos are affected?"], loaded["progress"]["open_questions"])

    def test_missing_progress_lists_default_to_empty(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            save_conversation_summary(root, "conv-1", "purpose")
            progress = load_conversation_summary(root, "conv-1")["progress"]
            self.assertEqual([], progress["completed"])
            self.assertEqual([], progress["decisions"])
            self.assertEqual([], progress["remaining"])
            self.assertEqual([], progress["open_questions"])

    def test_second_save_fully_overwrites_not_accumulates(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            save_conversation_summary(root, "conv-1", "purpose", completed=["Step 1."])
            save_conversation_summary(root, "conv-1", "purpose", completed=["Step 1.", "Step 2."])
            progress = load_conversation_summary(root, "conv-1")["progress"]
            self.assertEqual(["Step 1.", "Step 2."], progress["completed"])

    def test_rejects_invalid_status(self):
        with TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "status must be one of"):
                save_conversation_summary(Path(temp), "conv-1", "purpose", status="finished")

    def test_rejects_unsafe_conversation_id(self):
        with TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "not safe"):
                save_conversation_summary(Path(temp), "../conv", "purpose")

    def test_path_helper_matches_save_location(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(
                root / ".esc-ai" / "conversations" / "conv-1" / "summary.yaml",
                conversation_summary_path(root, "conv-1"),
            )


if __name__ == "__main__":
    unittest.main()
