import os
import socket
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.environment import check_prerequisites


def _plan(gates: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "task_id": "task-index-review",
        "profiles": [],
        "strategy": {"order": ["focused", "component", "impact", "final"], "stop_on_failure": True},
        "impact": {"graph": "esc-dependencies.json", "source_components": [], "consumer_components": []},
        "gates": gates,
    }


class CheckPrerequisitesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.workspace = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_no_prerequisites_declared_is_clean(self):
        plan = _plan([{"id": "final", "status": "ready", "checks": [{"id": "repository-tests", "command": ["true"]}]}])
        self.assertEqual([], check_prerequisites(plan, self.workspace))

    def test_gate_not_ready_is_never_checked(self):
        plan = _plan([{
            "id": "final", "status": "not-applicable",
            "checks": [{"id": "repository-tests", "command": ["true"], "prerequisites": [{"kind": "env", "name": "DOES_NOT_EXIST_XYZ"}]}],
        }])
        self.assertEqual([], check_prerequisites(plan, self.workspace))

    def test_unset_env_var_is_a_blocker(self):
        name = "ESC_AI_TEST_UNSET_VAR_XYZ"
        os.environ.pop(name, None)
        plan = _plan([{
            "id": "final", "status": "ready",
            "checks": [{"id": "repository-tests", "command": ["true"], "prerequisites": [{"kind": "env", "name": name}]}],
        }])
        blockers = check_prerequisites(plan, self.workspace)
        self.assertEqual(1, len(blockers))
        self.assertIn(f"env {name}", blockers[0])
        self.assertIn("final", blockers[0])
        self.assertIn("repository-tests", blockers[0])

    def test_set_env_var_is_satisfied(self):
        name = "ESC_AI_TEST_SET_VAR_XYZ"
        os.environ[name] = "token"
        try:
            plan = _plan([{
                "id": "final", "status": "ready",
                "checks": [{"id": "repository-tests", "command": ["true"], "prerequisites": [{"kind": "env", "name": name}]}],
            }])
            self.assertEqual([], check_prerequisites(plan, self.workspace))
        finally:
            del os.environ[name]

    def test_unreachable_tcp_port_is_a_blocker(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.close()  # closed immediately -- nothing listens on `port` anymore
        plan = _plan([{
            "id": "final", "status": "ready",
            "checks": [{
                "id": "repository-tests", "command": ["true"],
                "prerequisites": [{"kind": "tcp", "host": "127.0.0.1", "port": port}],
            }],
        }])
        blockers = check_prerequisites(plan, self.workspace, timeout=0.5)
        self.assertEqual(1, len(blockers))
        self.assertIn(f"tcp 127.0.0.1:{port}", blockers[0])

    def test_reachable_tcp_port_is_satisfied(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        try:
            plan = _plan([{
                "id": "final", "status": "ready",
                "checks": [{
                    "id": "repository-tests", "command": ["true"],
                    "prerequisites": [{"kind": "tcp", "host": "127.0.0.1", "port": port}],
                }],
            }])
            self.assertEqual([], check_prerequisites(plan, self.workspace, timeout=0.5))
        finally:
            listener.close()

    def test_missing_file_is_a_blocker(self):
        plan = _plan([{
            "id": "final", "status": "ready",
            "checks": [{
                "id": "repository-tests", "command": ["true"],
                "prerequisites": [{"kind": "file", "path": "does-not-exist.json"}],
            }],
        }])
        blockers = check_prerequisites(plan, self.workspace)
        self.assertEqual(1, len(blockers))
        self.assertIn("file does-not-exist.json", blockers[0])

    def test_existing_relative_file_is_satisfied(self):
        (self.workspace / "creds.json").write_text("{}", encoding="utf-8")
        plan = _plan([{
            "id": "final", "status": "ready",
            "checks": [{
                "id": "repository-tests", "command": ["true"],
                "prerequisites": [{"kind": "file", "path": "creds.json"}],
            }],
        }])
        self.assertEqual([], check_prerequisites(plan, self.workspace))

    def test_existing_absolute_file_is_satisfied(self):
        absolute = self.workspace / "creds.json"
        absolute.write_text("{}", encoding="utf-8")
        plan = _plan([{
            "id": "final", "status": "ready",
            "checks": [{
                "id": "repository-tests", "command": ["true"],
                "prerequisites": [{"kind": "file", "path": str(absolute)}],
            }],
        }])
        self.assertEqual([], check_prerequisites(plan, self.workspace))

    def test_multiple_unsatisfied_prerequisites_all_reported(self):
        plan = _plan([{
            "id": "final", "status": "ready",
            "checks": [{
                "id": "repository-tests", "command": ["true"],
                "prerequisites": [
                    {"kind": "env", "name": "ESC_AI_TEST_UNSET_VAR_ABC"},
                    {"kind": "file", "path": "does-not-exist.json"},
                ],
            }],
        }])
        os.environ.pop("ESC_AI_TEST_UNSET_VAR_ABC", None)
        blockers = check_prerequisites(plan, self.workspace)
        self.assertEqual(2, len(blockers))


if __name__ == "__main__":
    unittest.main()
