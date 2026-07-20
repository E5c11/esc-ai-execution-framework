import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.claude_code_adapter import ClaudeCodeError
from esc_exec.conversation import (
    HARD_CONTEXT_THRESHOLD, SOFT_CONTEXT_THRESHOLD,
    compact_conversation, context_consumption_ratio, context_window_from_usage, run_turn,
    suggest_groundable_answers_turn, suggest_unresolved_components, threshold_crossed,
)
from esc_exec.roadmap import load_conversation_summary


def _result_message(*, session_id="ses-created", text="Let's converge on a stack.", is_error=False,
                     usage=None, model_usage=None):
    """Mirrors the real `result`-type message shape from test_claude_code_adapter.py's
    `_stream_json`, extended with `modelUsage` -- verified live 2026-07-19/20 against
    claude-code 2.1.215, a real run reported:
    {"claude-sonnet-5": {..., "contextWindow": 1000000},
     "claude-haiku-4-5-20251001": {..., "contextWindow": 200000}}"""
    message = {
        "type": "result", "subtype": "success", "is_error": is_error,
        "result": text if not is_error else "model unavailable",
        "session_id": session_id, "num_turns": 2, "total_cost_usd": 0.0123,
        "usage": usage if usage is not None else {
            "input_tokens": 10, "output_tokens": 30, "cache_read_input_tokens": 40, "cache_creation_input_tokens": 2,
        },
    }
    if model_usage is not None:
        message["modelUsage"] = model_usage
    return message


def _stream_json(**kwargs):
    result = _result_message(**kwargs)
    return [
        {"type": "system", "subtype": "init", "session_id": result["session_id"], "tools": ["Read"]},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": result["result"]}]},
         "session_id": result["session_id"]},
        result,
    ]


class FakeClaudeCodeClient:
    def __init__(self, messages=None):
        self.messages = messages if messages is not None else _stream_json()
        self.prompts: list[str] = []
        self.tool_grants: list[list[str]] = []
        self.resume_ids: list[str | None] = []

    def run(self, directory, prompt, tools, model=None, resume_session_id=None):
        self.prompts.append(prompt)
        self.tool_grants.append(tools)
        self.resume_ids.append(resume_session_id)
        return self.messages


class FakeNoResultClient(FakeClaudeCodeClient):
    def __init__(self):
        super().__init__(messages=[{"type": "system", "subtype": "init", "session_id": "ses-created"}])


class FakeRaisingClient(FakeClaudeCodeClient):
    def run(self, directory, prompt, tools, model=None, resume_session_id=None):
        raise ClaudeCodeError("claude -p exited 1: rate limited")


REALISTIC_MODEL_USAGE = {
    "claude-haiku-4-5-20251001": {
        "inputTokens": 873, "outputTokens": 14, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
        "webSearchRequests": 0, "costUSD": 0.000943, "contextWindow": 200000, "maxOutputTokens": 32000,
    },
    "claude-sonnet-5": {
        "inputTokens": 10, "outputTokens": 818, "cacheReadInputTokens": 164129, "cacheCreationInputTokens": 9645,
        "webSearchRequests": 0, "costUSD": 0.1194087, "contextWindow": 1000000, "maxOutputTokens": 64000,
    },
}


class ContextWindowFromUsageTests(unittest.TestCase):
    def test_takes_max_window_across_models(self):
        outcome = _result_message(model_usage=REALISTIC_MODEL_USAGE)
        self.assertEqual(context_window_from_usage(outcome), 1000000)

    def test_missing_model_usage_is_none(self):
        self.assertIsNone(context_window_from_usage(_result_message()))

    def test_empty_model_usage_is_none(self):
        self.assertIsNone(context_window_from_usage(_result_message(model_usage={})))

    def test_malformed_entries_are_ignored(self):
        outcome = _result_message(model_usage={"claude-x": "not-a-dict", "claude-y": {"contextWindow": "nope"}})
        self.assertIsNone(context_window_from_usage(outcome))


class ContextConsumptionRatioTests(unittest.TestCase):
    def test_ratio_below_soft_threshold(self):
        outcome = _result_message(
            usage={"input_tokens": 100, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 100},
            model_usage={"claude-sonnet-5": {"contextWindow": 1000000}},
        )
        ratio = context_consumption_ratio(outcome)
        self.assertAlmostEqual(ratio, 300 / 1000000)
        self.assertIsNone(threshold_crossed(ratio))

    def test_ratio_at_soft_threshold(self):
        window = 1000
        consumed = int(window * SOFT_CONTEXT_THRESHOLD)
        outcome = _result_message(
            usage={"input_tokens": consumed, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            model_usage={"claude-sonnet-5": {"contextWindow": window}},
        )
        ratio = context_consumption_ratio(outcome)
        self.assertEqual(threshold_crossed(ratio), "soft")

    def test_ratio_at_hard_threshold(self):
        window = 1000
        consumed = int(window * HARD_CONTEXT_THRESHOLD)
        outcome = _result_message(
            usage={"input_tokens": consumed, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            model_usage={"claude-sonnet-5": {"contextWindow": window}},
        )
        ratio = context_consumption_ratio(outcome)
        self.assertEqual(threshold_crossed(ratio), "hard")

    def test_missing_usage_and_model_usage_is_none(self):
        outcome = _result_message(model_usage=None)
        self.assertIsNone(context_consumption_ratio(outcome))
        self.assertIsNone(threshold_crossed(context_consumption_ratio(outcome)))

    def test_none_ratio_never_counts_as_a_crossing(self):
        self.assertIsNone(threshold_crossed(None))


class RunTurnTests(unittest.TestCase):
    def test_successful_turn_returns_normalized_result(self):
        client = FakeClaudeCodeClient(messages=_stream_json(
            text="Use Compose Multiplatform.", session_id="ses-1", model_usage=REALISTIC_MODEL_USAGE,
        ))
        outcome = run_turn(client, Path("/repo"), "what stack?", ["Read"], resume_session_id=None, model=None)
        self.assertFalse(outcome["is_error"])
        self.assertEqual(outcome["text"], "Use Compose Multiplatform.")
        self.assertEqual(outcome["session_id"], "ses-1")
        self.assertIsNone(outcome["error_detail"])
        self.assertIsNotNone(outcome["context_ratio"])
        self.assertEqual(client.prompts, ["what stack?"])
        self.assertEqual(client.resume_ids, [None])

    def test_resume_session_id_is_threaded_through(self):
        client = FakeClaudeCodeClient()
        run_turn(client, Path("/repo"), "continue", ["Read"], resume_session_id="ses-existing")
        self.assertEqual(client.resume_ids, ["ses-existing"])

    def test_claude_code_error_fails_open(self):
        client = FakeRaisingClient()
        outcome = run_turn(client, Path("/repo"), "prompt", [], resume_session_id="ses-1")
        self.assertTrue(outcome["is_error"])
        self.assertEqual(outcome["session_id"], "ses-1")
        self.assertIn("rate limited", outcome["error_detail"])
        self.assertIsNone(outcome["context_ratio"])
        self.assertIsNone(outcome["threshold"])

    def test_no_result_message_fails_open(self):
        client = FakeNoResultClient()
        outcome = run_turn(client, Path("/repo"), "prompt", [])
        self.assertTrue(outcome["is_error"])
        self.assertIn("result", outcome["error_detail"])

    def test_error_result_message_fails_open(self):
        client = FakeClaudeCodeClient(messages=_stream_json(is_error=True, session_id="ses-2"))
        outcome = run_turn(client, Path("/repo"), "prompt", [])
        self.assertTrue(outcome["is_error"])
        self.assertEqual(outcome["session_id"], "ses-2")
        self.assertIn("model unavailable", outcome["error_detail"])

    def test_missing_model_usage_still_returns_success_with_none_ratio(self):
        client = FakeClaudeCodeClient(messages=_stream_json(model_usage=None))
        outcome = run_turn(client, Path("/repo"), "prompt", [])
        self.assertFalse(outcome["is_error"])
        self.assertIsNone(outcome["context_ratio"])
        self.assertIsNone(outcome["threshold"])

    def test_hard_threshold_surfaced_on_successful_turn(self):
        window = 1000
        consumed = int(window * HARD_CONTEXT_THRESHOLD)
        client = FakeClaudeCodeClient(messages=_stream_json(
            usage={"input_tokens": consumed, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            model_usage={"claude-sonnet-5": {"contextWindow": window}},
        ))
        outcome = run_turn(client, Path("/repo"), "prompt", [])
        self.assertEqual(outcome["threshold"], "hard")


class CompactConversationTests(unittest.TestCase):
    def test_saves_summary_and_returns_roadmap_proposal(self):
        payload = {
            "progress": {
                "completed": ["picked Compose Multiplatform"],
                "decisions": ["use Compose over Flutter -- team already knows Kotlin"],
                "remaining": ["scaffold initial modules"],
                "open_questions": ["monorepo or separate repos per platform?"],
            },
            "roadmap": {
                "purpose": "cross-platform expense tracker",
                "current_stage": "stack chosen, no code yet",
                "direction": "scaffold Compose Multiplatform modules next",
                "durable_decisions": ["Compose Multiplatform over Flutter"],
            },
        }
        client = FakeClaudeCodeClient(messages=_stream_json(text=json.dumps(payload), session_id="ses-1"))
        with TemporaryDirectory() as temp:
            repository = Path(temp)
            result = compact_conversation(client, repository, "plan-feature-x", "ses-1", "plan feature X")

            self.assertFalse(result.get("is_error", False))
            self.assertEqual(result["progress"], payload["progress"])
            self.assertEqual(result["roadmap_proposal"], payload["roadmap"])
            self.assertEqual(result["session_id"], "ses-1")

            saved = load_conversation_summary(repository, "plan-feature-x")
            self.assertEqual(saved["progress"], payload["progress"])
            self.assertEqual(saved["conversation_summary"]["status"], "in-progress")
        self.assertEqual(client.resume_ids, ["ses-1"])

    def test_commentary_prefixed_json_is_still_parsed(self):
        payload = {"progress": {"completed": ["done thing"]}}
        text = "Sure, here's the compacted summary:\n```json\n" + json.dumps(payload) + "\n```"
        client = FakeClaudeCodeClient(messages=_stream_json(text=text, session_id="ses-1"))
        with TemporaryDirectory() as temp:
            result = compact_conversation(client, Path(temp), "conv-1", "ses-1", "purpose")
        self.assertEqual(result["progress"]["completed"], ["done thing"])
        self.assertIsNone(result["roadmap_proposal"])

    def test_roadmap_key_omitted_means_no_proposal(self):
        payload = {"progress": {"completed": ["a"], "decisions": [], "remaining": [], "open_questions": []}}
        client = FakeClaudeCodeClient(messages=_stream_json(text=json.dumps(payload), session_id="ses-1"))
        with TemporaryDirectory() as temp:
            result = compact_conversation(client, Path(temp), "conv-1", "ses-1", "purpose")
        self.assertIsNone(result["roadmap_proposal"])

    def test_malformed_progress_items_are_dropped_not_invented(self):
        payload = {
            "progress": {"completed": ["ok", 42, None], "decisions": "not-a-list", "remaining": [], "open_questions": []},
        }
        client = FakeClaudeCodeClient(messages=_stream_json(text=json.dumps(payload), session_id="ses-1"))
        with TemporaryDirectory() as temp:
            result = compact_conversation(client, Path(temp), "conv-1", "ses-1", "purpose")
        self.assertEqual(result["progress"]["completed"], ["ok"])
        self.assertEqual(result["progress"]["decisions"], [])

    def test_existing_roadmap_is_included_in_prompt(self):
        client = FakeClaudeCodeClient(messages=_stream_json(text='{"progress": {}}', session_id="ses-1"))
        existing = {"project_roadmap": {"purpose": "an app", "current_stage": "mvp", "direction": "ship it",
                                         "durable_decisions": ["use Postgres"]}}
        with TemporaryDirectory() as temp:
            compact_conversation(client, Path(temp), "conv-1", "ses-1", "purpose", existing_roadmap=existing)
        self.assertIn("use Postgres", client.prompts[0])
        self.assertIn("mvp", client.prompts[0])

    def test_claude_code_error_fails_open_with_empty_progress_summary(self):
        client = FakeRaisingClient()
        with TemporaryDirectory() as temp:
            repository = Path(temp)
            result = compact_conversation(client, repository, "conv-1", "ses-1", "purpose")
            self.assertIsNone(result["roadmap_proposal"])
            self.assertEqual(result["progress"], {"completed": [], "decisions": [], "remaining": [], "open_questions": []})
            saved = load_conversation_summary(repository, "conv-1")
            self.assertIsNotNone(saved)
            self.assertEqual(saved["progress"]["completed"], [])

    def test_unparseable_json_fails_open_with_empty_progress_summary(self):
        client = FakeClaudeCodeClient(messages=_stream_json(text="not json at all", session_id="ses-1"))
        with TemporaryDirectory() as temp:
            result = compact_conversation(client, Path(temp), "conv-1", "ses-1", "purpose")
        self.assertIsNone(result["roadmap_proposal"])
        self.assertEqual(result["progress"]["completed"], [])


class SuggestUnresolvedComponentsTests(unittest.TestCase):
    def test_empty_unresolved_list_short_circuits_without_a_call(self):
        client = FakeClaudeCodeClient()
        with TemporaryDirectory() as temp:
            result = suggest_unresolved_components(client, Path(temp), [])
        self.assertEqual({}, result["resolved"])
        self.assertEqual([], client.prompts)

    def test_resolves_identifiers_to_real_directories(self):
        with TemporaryDirectory() as temp:
            repository = Path(temp)
            (repository / "error-core").mkdir()
            payload = {":arrow-errors-core": "error-core"}
            client = FakeClaudeCodeClient(messages=_stream_json(text=json.dumps(payload), session_id="ses-1"))
            result = suggest_unresolved_components(client, repository, [":arrow-errors-core"])
        self.assertEqual({":arrow-errors-core": "error-core"}, result["resolved"])
        self.assertEqual("ses-1", result["session_id"])
        self.assertEqual([None], client.resume_ids)

    def test_prompt_never_mentions_gradle_or_npm(self):
        with TemporaryDirectory() as temp:
            client = FakeClaudeCodeClient(messages=_stream_json(text="{}", session_id="ses-1"))
            suggest_unresolved_components(client, Path(temp), [":ghost"])
        self.assertNotIn("gradle", client.prompts[0].lower())
        self.assertNotIn("npm", client.prompts[0].lower())
        self.assertIn(":ghost", client.prompts[0])

    def test_answer_for_unlisted_identifier_is_dropped(self):
        with TemporaryDirectory() as temp:
            repository = Path(temp)
            (repository / "other").mkdir()
            payload = {":not-asked-about": "other"}
            client = FakeClaudeCodeClient(messages=_stream_json(text=json.dumps(payload), session_id="ses-1"))
            result = suggest_unresolved_components(client, repository, [":ghost"])
        self.assertEqual({}, result["resolved"])

    def test_answer_pointing_at_nonexistent_directory_is_dropped(self):
        with TemporaryDirectory() as temp:
            repository = Path(temp)
            payload = {":ghost": "does-not-exist"}
            client = FakeClaudeCodeClient(messages=_stream_json(text=json.dumps(payload), session_id="ses-1"))
            result = suggest_unresolved_components(client, repository, [":ghost"])
        self.assertEqual({}, result["resolved"])

    def test_client_error_fails_open(self):
        with TemporaryDirectory() as temp:
            result = suggest_unresolved_components(FakeRaisingClient(), Path(temp), [":ghost"])
        self.assertEqual({}, result["resolved"])

    def test_resumes_an_existing_session_when_given_one(self):
        with TemporaryDirectory() as temp:
            client = FakeClaudeCodeClient(messages=_stream_json(text="{}", session_id="ses-1"))
            suggest_unresolved_components(client, Path(temp), [":ghost"], resume_session_id="ses-1")
        self.assertEqual(["ses-1"], client.resume_ids)


class SuggestGroundableAnswersTurnTests(unittest.TestCase):
    def test_no_applicable_components_short_circuits_without_a_call(self):
        client = FakeClaudeCodeClient()
        with TemporaryDirectory() as temp:
            result = suggest_groundable_answers_turn(client, Path(temp), {"purpose": set(), "frameworks_targets": set()})
        self.assertEqual({}, result["suggestions"])
        self.assertEqual([], client.prompts)

    def test_resumes_the_given_session_for_a_cheap_second_turn(self):
        payload = {"core-api": {"purpose": "Owns the core API."}}
        client = FakeClaudeCodeClient(messages=_stream_json(text=json.dumps(payload), session_id="ses-1"))
        with TemporaryDirectory() as temp:
            result = suggest_groundable_answers_turn(
                client, Path(temp), {"purpose": {"core-api"}, "frameworks_targets": set()},
                resume_session_id="ses-1",
            )
        self.assertEqual({"core-api": {"purpose": "Owns the core API."}}, result["suggestions"])
        self.assertEqual("ses-1", result["session_id"])
        self.assertEqual(["ses-1"], client.resume_ids)

    def test_mixed_applicability_never_leaks_a_field_across_components(self):
        payload = {
            "core-api": {"purpose": "Owns the core API.", "frameworks": {"network": "ktor"}},
            "feature": {"purpose": "Owns the feature.", "frameworks": {"network": "ktor"}},
        }
        client = FakeClaudeCodeClient(messages=_stream_json(text=json.dumps(payload), session_id="ses-1"))
        with TemporaryDirectory() as temp:
            result = suggest_groundable_answers_turn(
                client, Path(temp), {"purpose": {"core-api", "feature"}, "frameworks_targets": {"core-api"}},
            )
        self.assertEqual({
            "core-api": {"purpose": "Owns the core API.", "frameworks": {"network": "ktor"}},
            "feature": {"purpose": "Owns the feature."},
        }, result["suggestions"])

    def test_turn_error_fails_open(self):
        with TemporaryDirectory() as temp:
            result = suggest_groundable_answers_turn(
                FakeRaisingClient(), Path(temp), {"purpose": {"core-api"}, "frameworks_targets": set()},
            )
        self.assertEqual({}, result["suggestions"])


if __name__ == "__main__":
    unittest.main()
