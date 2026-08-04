import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from esc_exec.model import ManifestState
from esc_exec.registry import (
    RENAMED_FRAMEWORK_IDS, active_provider, add_ecosystem, add_route, default_policy_id,
    default_registry_path, migrate_legacy_registry, resolve_route, set_default_policy,
    set_provider, validate_registry,
)
from esc_exec.yaml_io import load_yaml, write_yaml


class RegistryTests(unittest.TestCase):
    def test_add_resolve_and_validate_route(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "config/repositories.yaml"
            repository = root / "repo"
            repository.mkdir()
            add_route(registry, "repositories", "sample", repository)
            self.assertEqual(repository.resolve(), resolve_route(registry, "repositories", "sample"))
            self.assertEqual(ManifestState.VALID, validate_registry(registry).state)

    def test_missing_target_is_stale(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "repositories.yaml"
            target = root / "repo"
            target.mkdir()
            add_route(registry, "repositories", "sample", target)
            target.rmdir()
            self.assertEqual(ManifestState.STALE, validate_registry(registry).state)

    def test_missing_route_requests_registration(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "repositories.yaml"
            with self.assertRaisesRegex(KeyError, "esc-exec route add repository sample"):
                resolve_route(registry, "repositories", "sample")

    def test_resolving_renamed_framework_reports_migration(self):
        old_id, new_id = next(iter(RENAMED_FRAMEWORK_IDS.items()))
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "repositories.yaml"
            framework = root / "framework"
            framework.mkdir()
            add_route(registry, "frameworks", new_id, framework)
            with self.assertRaisesRegex(KeyError, f"renamed to `{new_id}`"):
                resolve_route(registry, "frameworks", old_id)

    def test_registered_renamed_framework_id_is_stale(self):
        old_id = next(iter(RENAMED_FRAMEWORK_IDS))
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "repositories.yaml"
            framework = root / "framework"
            framework.mkdir()
            add_route(registry, "frameworks", old_id, framework)
            result = validate_registry(registry)
            self.assertEqual(ManifestState.STALE, result.state)
            self.assertTrue(any("renamed framework ID" in message for message in result.messages))

    def test_ecosystem_of_registered_repositories_is_valid(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "repositories.yaml"
            for repo_id in ("ampm-backend", "ampm-mobile"):
                repository = root / repo_id
                repository.mkdir()
                add_route(registry, "repositories", repo_id, repository)
            add_ecosystem(registry, "ampm", ["ampm-backend", "ampm-mobile"])
            self.assertEqual(ManifestState.VALID, validate_registry(registry).state)

    def test_ecosystem_referencing_unregistered_repository_is_invalid(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "repositories.yaml"
            repository = root / "ampm-backend"
            repository.mkdir()
            add_route(registry, "repositories", "ampm-backend", repository)
            add_ecosystem(registry, "ampm", ["ampm-backend", "ampm-mobile"])
            result = validate_registry(registry)
            self.assertEqual(ManifestState.INVALID, result.state)
            self.assertTrue(any("references unregistered repository: ampm-mobile" in message for message in result.messages))


    def test_stale_route_message_includes_repair_command(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "repositories.yaml"
            target = root / "repo"
            target.mkdir()
            add_route(registry, "repositories", "sample", target)
            target.rmdir()
            result = validate_registry(registry)
            self.assertTrue(any("Run: esc-exec route add repository sample" in message for message in result.messages))

    def test_default_registry_path_uses_system_yaml(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("sys.platform", "linux"):
                with TemporaryDirectory() as temp:
                    os.environ["XDG_CONFIG_HOME"] = temp
                    self.assertEqual(Path(temp) / "esc-ai" / "system.yaml", default_registry_path())

    def test_migrate_legacy_registry_migrates_existing_file(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = root / "repositories.yaml"
            write_yaml(legacy, {"schema_version": 1, "repositories": {"sample": {"path": "/tmp/sample"}}, "frameworks": {}})
            new_path = root / "system.yaml"
            result = migrate_legacy_registry(new_path)
            self.assertEqual(new_path, result)
            self.assertEqual({"path": "/tmp/sample"}, load_yaml(new_path)["repositories"]["sample"])

    def test_migrate_legacy_registry_is_noop_when_new_path_exists(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = root / "repositories.yaml"
            write_yaml(legacy, {"schema_version": 1, "repositories": {}, "frameworks": {}})
            new_path = root / "system.yaml"
            write_yaml(new_path, {"schema_version": 1, "repositories": {}, "frameworks": {}})
            self.assertIsNone(migrate_legacy_registry(new_path))

    def test_migrate_legacy_registry_is_noop_when_no_legacy_file(self):
        with TemporaryDirectory() as temp:
            self.assertIsNone(migrate_legacy_registry(Path(temp) / "system.yaml"))

    def test_orchestrator_ui_and_credentials_sections_are_valid(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "system.yaml"
            write_yaml(registry, {
                "schema_version": 1,
                "repositories": {},
                "frameworks": {},
                "orchestrator": {"endpoint": "http://127.0.0.1:8042"},
                "ui": {"theme": "dark"},
                "credentials": {"provider": "env"},
            })
            self.assertEqual(ManifestState.VALID, validate_registry(registry).state)

    def test_unknown_field_inside_credentials_is_invalid(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "system.yaml"
            write_yaml(registry, {
                "schema_version": 1,
                "repositories": {},
                "frameworks": {},
                "credentials": {"provider": "env", "secret": "should-not-be-here"},
            })
            result = validate_registry(registry)
            self.assertEqual(ManifestState.INVALID, result.state)
            self.assertTrue(any("credentials must be a mapping" in message for message in result.messages))

    def test_no_provider_configured_returns_none(self):
        with TemporaryDirectory() as temp:
            self.assertIsNone(active_provider(Path(temp) / "system.yaml"))

    def test_set_provider_makes_it_active(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "system.yaml"
            set_provider(registry, "claude", "subscription")
            self.assertEqual({"id": "claude", "route": "subscription"}, active_provider(registry))
            self.assertEqual(ManifestState.VALID, validate_registry(registry).state)

    def test_setting_a_second_provider_switches_active(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "system.yaml"
            set_provider(registry, "claude", "subscription")
            set_provider(registry, "openai", "api-key")
            self.assertEqual({"id": "openai", "route": "api-key"}, active_provider(registry))
            # the previously-active provider stays connected, just no longer active
            data = load_yaml(registry)
            self.assertEqual({"route": "subscription"}, data["providers"]["claude"])

    def test_unknown_provider_name_is_rejected(self):
        with TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "unknown provider"):
                set_provider(Path(temp) / "system.yaml", "mistral", "api-key")

    def test_subscription_route_rejected_for_non_subscription_capable_provider(self):
        # No real provider is currently known-but-not-subscription-capable (gemini
        # was removed from KNOWN_PROVIDERS entirely -- see
        # plan/reintroduce-gemini-provider.md), so this exercises the branch via a
        # synthetic provider rather than skipping coverage of it.
        with TemporaryDirectory() as temp:
            with patch("esc_exec.registry.KNOWN_PROVIDERS", ("acme",)):
                with self.assertRaisesRegex(ValueError, "no subscription-route adapter"):
                    set_provider(Path(temp) / "system.yaml", "acme", "subscription")

    def test_invalid_route_value_in_file_is_invalid(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "system.yaml"
            write_yaml(registry, {
                "schema_version": 1, "repositories": {}, "frameworks": {},
                "providers": {"claude": {"route": "bearer-token"}, "active": "claude"},
            })
            result = validate_registry(registry)
            self.assertEqual(ManifestState.INVALID, result.state)
            self.assertTrue(any("providers.claude.route must be one of" in message for message in result.messages))

    def test_subscription_route_for_non_capable_provider_in_file_is_invalid(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "system.yaml"
            write_yaml(registry, {
                "schema_version": 1, "repositories": {}, "frameworks": {},
                "providers": {"gemini": {"route": "subscription"}, "active": "gemini"},
            })
            result = validate_registry(registry)
            self.assertEqual(ManifestState.INVALID, result.state)
            self.assertTrue(any("no subscription-route adapter" in message for message in result.messages))

    def test_active_pointing_at_unconfigured_provider_is_invalid(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "system.yaml"
            write_yaml(registry, {
                "schema_version": 1, "repositories": {}, "frameworks": {},
                "providers": {"active": "claude"},
            })
            result = validate_registry(registry)
            self.assertEqual(ManifestState.INVALID, result.state)
            self.assertTrue(any("references unconfigured provider: claude" in message for message in result.messages))

    def test_no_default_policy_configured_returns_none(self):
        with TemporaryDirectory() as temp:
            self.assertIsNone(default_policy_id(Path(temp) / "system.yaml"))

    def test_set_default_policy_records_it(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "system.yaml"
            set_default_policy(registry, "readonly-review")
            self.assertEqual("readonly-review", default_policy_id(registry))
            self.assertEqual(ManifestState.VALID, validate_registry(registry).state)

    def test_setting_default_policy_twice_overwrites_it(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "system.yaml"
            set_default_policy(registry, "readonly-review")
            set_default_policy(registry, "standard-autonomous")
            self.assertEqual("standard-autonomous", default_policy_id(registry))

    def test_blank_default_policy_is_rejected(self):
        with TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "non-empty string"):
                set_default_policy(Path(temp) / "system.yaml", "  ")

    def test_non_string_default_policy_in_file_is_invalid(self):
        with TemporaryDirectory() as temp:
            registry = Path(temp) / "system.yaml"
            write_yaml(registry, {
                "schema_version": 1, "repositories": {}, "frameworks": {}, "default_policy": 7,
            })
            result = validate_registry(registry)
            self.assertEqual(ManifestState.INVALID, result.state)
            self.assertTrue(any("default_policy must be a non-empty string" in message for message in result.messages))


if __name__ == "__main__":
    unittest.main()
