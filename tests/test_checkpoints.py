import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.checkpoints import create_checkpoint, inspect_checkpoint, update_checkpoint
from esc_exec.contracts import validate_contract
from esc_exec.model import ManifestState
from esc_exec.yaml_io import load_yaml, write_yaml


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.task = self.root / ".esc-ai/workflows/active/task-1/task.yaml"
        write_yaml(self.task, {
            "schema_version": 1,
            "task": {"id": "task-1", "title": "Test", "objective": "Finish the work", "repository": "repo", "status": "active"},
            "scope": {"components": ["content"]},
            "completion_conditions": ["done"],
        })

    def tearDown(self):
        self.temporary.cleanup()

    def test_creates_updates_and_inspects_canonical_checkpoint(self):
        path = create_checkpoint(
            self.root, self.task, run_id="run-1", status="blocked",
            completed=["Located the failing boundary."], decisions=["Keep stable rule ID."],
            remaining=["Fix dependency."], blockers=["Needs API decision."],
            artifacts=[".esc-ai/runs/run-1/architecture.json"], last_event_sequence=4,
        )
        self.assertEqual(self.root / ".esc-ai/workflows/active/task-1/checkpoint.yaml", path)
        self.assertEqual(ManifestState.VALID, validate_contract("checkpoint", path).state)

        update_checkpoint(
            self.root, "task-1", status="ready-to-resume", clear_blockers=True,
            completed=["Located the failing boundary."], remaining=["Run component tests."],
        )
        document = load_yaml(path)
        self.assertEqual("ready-to-resume", document["checkpoint"]["status"])
        self.assertEqual([], document["progress"]["blockers"])
        self.assertEqual(1, document["progress"]["completed"].count("Located the failing boundary."))
        compact = json.loads(inspect_checkpoint(self.root, "task-1"))
        self.assertEqual("task-1", compact["checkpoint"]["task_id"])

    def test_invalid_update_is_rolled_back(self):
        path = create_checkpoint(self.root, self.task, remaining=["Continue."])
        with self.assertRaisesRegex(ValueError, "blocked checkpoints"):
            update_checkpoint(self.root, "task-1", status="blocked")
        self.assertEqual("active", load_yaml(path)["checkpoint"]["status"])

    def test_rejects_unsafe_task_id(self):
        with self.assertRaisesRegex(ValueError, "not safe"):
            inspect_checkpoint(self.root, "../task")
