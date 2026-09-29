"""Verification must run in the tree the agent changed (esc_exec.worktree.verification_root), while a run directory
that lives in the live checkout is still reported relative to it."""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from esc_exec.verification_execution import execute_verification_plan
from esc_exec.worktree import ensure_worktree, verification_root, worktree_path

MARKER_CHECK = [sys.executable, "-c", "import sys; sys.exit(0 if open('marker.txt').read().strip() == 'agent' else 1)"]


def git(repository: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repository), *args], capture_output=True, text=True, check=True)


def plan(command):
    return {
        "schema_version": 1, "task_id": "t", "profiles": [],
        "strategy": {"order": ["focused", "component", "impact", "final"], "stop_on_failure": True},
        "impact": {"graph": "g.json", "source_components": [], "consumer_components": []},
        "gates": [
            {"id": "focused", "status": "not-applicable", "checks": []},
            {"id": "component", "status": "not-applicable", "checks": []},
            {"id": "impact", "status": "not-applicable", "checks": []},
            {"id": "final", "status": "ready", "checks": [{"id": "marker", "command": command}]},
        ],
    }


class RunsInTheGivenTreeTests(unittest.TestCase):
    def test_commands_run_in_workspace_root_and_logs_are_reported_relative_to_the_live_checkout(self):
        with TemporaryDirectory() as temp:
            live, tree = Path(temp) / "live", Path(temp) / "worktree"
            live.mkdir()
            tree.mkdir()
            (live / "marker.txt").write_text("before\n")
            (tree / "marker.txt").write_text("agent\n")
            run_dir = live / ".esc-ai" / "runs" / "run-1"
            result = execute_verification_plan(plan(MARKER_CHECK), tree, run_dir, relative_to=live)
            self.assertEqual("passed", result["status"])  # it read the tree's marker, not the live one
            check = next(c for g in result["gates"] for c in g["checks"] if c["id"] == "marker")
            self.assertEqual(".esc-ai/runs/run-1/logs/final-marker.stdout.log", check["stdout_path"])
            self.assertTrue((live / check["stdout_path"]).is_file())

    def test_the_same_check_fails_against_the_live_checkout(self):
        with TemporaryDirectory() as temp:
            live = Path(temp) / "live"
            live.mkdir()
            (live / "marker.txt").write_text("before\n")
            result = execute_verification_plan(plan(MARKER_CHECK), live, live / ".esc-ai" / "runs" / "run-1")
            self.assertEqual("failed", result["status"])

    def test_a_run_directory_outside_the_reporting_root_is_still_an_error(self):
        with TemporaryDirectory() as temp, TemporaryDirectory() as elsewhere:
            with self.assertRaises(ValueError):
                execute_verification_plan(plan(MARKER_CHECK), Path(temp), Path(elsewhere) / "run")


class VerificationRootTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "T")
        (self.repo / "a.txt").write_text("x\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")
        self.run_dir = self.repo / ".esc-ai" / "runs" / "run-1"
        self.run_dir.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def record(self, binding):
        (self.run_dir / "run.json").write_text(json.dumps({"bindings": binding}))

    def test_a_kept_worktree_is_where_verification_runs(self):
        ensure_worktree(self.repo, "t")
        self.record({"worktree": {"branch": "esc-ai-task-t", "kept": True}})
        self.assertEqual(worktree_path(self.repo, "t"), verification_root(self.repo, "t", self.run_dir))

    def test_a_worktree_that_was_not_kept_means_nothing_changed_so_the_repository_is_verified(self):
        self.record({"worktree": {"branch": "esc-ai-task-t", "kept": False}})
        self.assertEqual(self.repo, verification_root(self.repo, "t", self.run_dir))

    def test_a_run_with_no_worktree_binding_edited_the_live_checkout(self):
        for name, content in (("no run.json", None), ("no worktree key", {}), ("unreadable", "not json")):
            with self.subTest(case=name):
                path = self.run_dir / "run.json"
                if content is None:
                    path.unlink(missing_ok=True)
                elif isinstance(content, str):
                    path.write_text(content)
                else:
                    self.record(content)
                self.assertEqual(self.repo, verification_root(self.repo, "t", self.run_dir))

    def test_a_kept_binding_whose_worktree_is_gone_falls_back_to_the_repository(self):
        self.record({"worktree": {"branch": "esc-ai-task-t", "kept": True}})
        self.assertEqual(self.repo, verification_root(self.repo, "t", self.run_dir))


if __name__ == "__main__":
    unittest.main()
