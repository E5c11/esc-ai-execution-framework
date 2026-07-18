from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.contracts import validate_contract
from esc_exec.json_io import write_json
from esc_exec.measurement import compare_efficiency, process_metrics, token_metrics
from esc_exec.model import ManifestState


def metrics(run_id, tokens, elapsed, tools, context, rework=0):
    return {
        "schema_version": 1,
        "run": {"id": run_id, "task_id": "task", "provider": "test", "status": "succeeded"},
        "context": {"bytes": context, "components": 1, "paths": 0, "references": 0},
        "execution": {"elapsed_ms": elapsed, "tool_calls": tools, "read_calls": tools, "agent_messages": 1, "rework_events": rework},
        "tokens": {"status": "reported", "input": tokens - 100, "output": 100, "reasoning": 0, "cache_read": 0, "cache_write": 0, "total": tokens},
        "generated_at": "2026-07-17T12:00:00Z",
    }


class MeasurementTests(unittest.TestCase):
    def test_compares_measured_averages_and_savings(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            baseline = root / "baseline.json"
            candidate = root / "candidate.json"
            output = root / "comparison.json"
            write_json(baseline, metrics("baseline", 1000, 100, 10, 5000, 2))
            write_json(candidate, metrics("candidate", 800, 120, 6, 3000, 1))
            comparison = compare_efficiency([baseline], [candidate], output)
            self.assertEqual(20.0, comparison["dimensions"]["tokens"]["change_percent"])
            self.assertEqual("improved", comparison["dimensions"]["tool_calls"]["status"])
            self.assertEqual("regressed", comparison["dimensions"]["elapsed_ms"]["status"])
            self.assertEqual(ManifestState.VALID, validate_contract("efficiency-comparison", output).state)

    def test_missing_provider_usage_is_explicitly_unavailable(self):
        result = token_metrics({"info": {}})
        self.assertEqual("unavailable", result["status"])
        self.assertIsNone(result["total"])

    def test_process_metrics_computes_elapsed_seconds_and_validates(self):
        with TemporaryDirectory() as temp:
            output = Path(temp) / "process-metrics.json"
            document = process_metrics(
                "onboarding", "ampm-backend",
                "2026-07-17T12:00:00Z", "2026-07-17T12:03:04.500Z",
                questions_asked=3, questions_answered=3,
            )
            self.assertEqual(184.5, document["elapsed_seconds"])
            write_json(output, document)
            self.assertEqual(ManifestState.VALID, validate_contract("process-metrics", output).state)

    def test_process_metrics_rejects_unsupported_kind(self):
        with self.assertRaisesRegex(ValueError, "unsupported process kind"):
            process_metrics("execution", "x", "2026-07-17T12:00:00Z", "2026-07-17T12:00:00Z", 0, 0)

    def test_process_metrics_answered_exceeding_asked_is_invalid(self):
        with TemporaryDirectory() as temp:
            output = Path(temp) / "process-metrics.json"
            document = process_metrics(
                "planning", "init-1", "2026-07-17T12:00:00Z", "2026-07-17T12:00:05Z",
                questions_asked=1, questions_answered=2,
            )
            write_json(output, document)
            result = validate_contract("process-metrics", output)
            self.assertEqual(ManifestState.INVALID, result.state)
            self.assertTrue(any("cannot exceed" in message for message in result.messages))
