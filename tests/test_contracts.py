from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.contracts import CONTRACT_FORMATS, validate_contract, validate_contract_set
from esc_exec.model import ManifestState


class ContractTests(unittest.TestCase):
    def test_connected_examples_are_valid(self):
        examples = Path(__file__).parents[1] / "examples/contracts"
        paths = {
            "task": examples / "task.yaml",
            "workspace": examples / "workspace.yaml",
            "adapter": examples / "adapter.yaml",
            "policy": examples / "policy.yaml",
            "checkpoint": examples / "checkpoint.yaml",
            "run": examples / "run.json",
            "artifact": examples / "artifact.json",
            "event": examples / "events.jsonl",
            "verification-summary": examples / "verification-summary.json",
            "task-context": examples / "task-context.json",
            "verification-plan": examples / "verification-plan.json",
            "architecture-report": examples / "architecture-report.json",
            "dependency-graph": examples / "dependency-graph.json",
            "impact-analysis": examples / "impact-analysis.json",
            "run-metrics": examples / "run-metrics.json",
            "efficiency-comparison": examples / "efficiency-comparison.json",
        }
        self.assertEqual(set(CONTRACT_FORMATS), set(paths))
        for kind, path in paths.items():
            with self.subTest(kind=kind):
                self.assertEqual(ManifestState.VALID, validate_contract(kind, path).state)
        self.assertTrue(all(
            result.state == ManifestState.VALID
            for result in validate_contract_set(examples)
        ))

    def test_contract_set_rejects_broken_run_reference(self):
        import shutil
        import json
        with TemporaryDirectory() as temp:
            target = Path(temp) / "contracts"
            shutil.copytree(Path(__file__).parents[1] / "examples/contracts", target)
            run_path = target / "run.json"
            run = json.loads(run_path.read_text(encoding="utf-8"))
            run["run"]["task_id"] = "wrong-task"
            run_path.write_text(json.dumps(run), encoding="utf-8")
            results = validate_contract_set(target)
            self.assertEqual(ManifestState.INVALID, results[-1].state)
            self.assertTrue(any("task.id" in message for message in results[-1].messages))

    def test_invalid_lifecycle_status_is_rejected(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "run.json"
            path.write_text(
                '{"schema_version":1,"run":{"id":"r","task_id":"t","status":"done","created_at":"x"},'
                '"bindings":{},"events":"events.jsonl","artifacts":[]}',
                encoding="utf-8",
            )
            result = validate_contract("run", path)
            self.assertEqual(ManifestState.INVALID, result.state)
            self.assertTrue(any("run.status" in message for message in result.messages))

    def test_event_sequences_must_increase(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            event = '{"schema_version":1,"event":{"id":"e","run_id":"r","sequence":0,"timestamp":"x","type":"run.started","actor":"system","payload":{}}}'
            path.write_text(event + "\n" + event + "\n", encoding="utf-8")
            result = validate_contract("event", path)
            self.assertEqual(ManifestState.INVALID, result.state)
            self.assertTrue(any("monotonically" in message for message in result.messages))

    def test_missing_contract_is_incomplete(self):
        result = validate_contract("task", Path("does-not-exist.yaml"))
        self.assertEqual(ManifestState.INCOMPLETE, result.state)


if __name__ == "__main__":
    unittest.main()
