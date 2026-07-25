import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.worktree import (
    WorktreeError, commit_worktree_changes, diff_summary, ensure_excluded, ensure_worktree,
    finalize_worktree, has_commits_ahead, has_uncommitted_changes, merge_worktree, remove_worktree,
    worktree_branch, worktree_path,
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)


def _init_repository(root: Path) -> Path:
    repository = root / "repo"
    repository.mkdir()
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test")
    (repository / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repository, "add", "-A")
    _git(repository, "commit", "-q", "-m", "initial")
    return repository


class WorktreeTests(unittest.TestCase):
    def test_ensure_worktree_creates_worktree_and_branch(self):
        with TemporaryDirectory() as temp:
            repository = _init_repository(Path(temp))
            path = ensure_worktree(repository, "task-1")
            self.assertTrue(path.is_dir())
            self.assertEqual(worktree_path(repository, "task-1"), path)
            self.assertTrue((path / "README.md").is_file())
            branches = _git(repository, "branch", "--list", worktree_branch("task-1")).stdout
            self.assertIn(worktree_branch("task-1"), branches)

    def test_ensure_worktree_is_idempotent_across_retries(self):
        with TemporaryDirectory() as temp:
            repository = _init_repository(Path(temp))
            first = ensure_worktree(repository, "task-1")
            (first / "scratch.txt").write_text("in progress\n", encoding="utf-8")
            second = ensure_worktree(repository, "task-1")
            self.assertEqual(first, second)
            self.assertTrue((second / "scratch.txt").is_file())

    def test_ensure_worktree_reattaches_existing_branch_if_directory_was_removed(self):
        with TemporaryDirectory() as temp:
            repository = _init_repository(Path(temp))
            ensure_worktree(repository, "task-1")
            remove_worktree(repository, "task-1", delete_branch=False)
            path = ensure_worktree(repository, "task-1")
            self.assertTrue(path.is_dir())
            branches = _git(repository, "branch", "--list", worktree_branch("task-1")).stdout
            self.assertEqual(1, branches.count(worktree_branch("task-1")))

    def test_ensure_excluded_adds_entry_once(self):
        with TemporaryDirectory() as temp:
            repository = _init_repository(Path(temp))
            ensure_excluded(repository)
            ensure_excluded(repository)
            content = (repository / ".git" / "info" / "exclude").read_text(encoding="utf-8")
            self.assertEqual(1, content.count("**/.esc-ai/worktrees/"))

    def test_ensure_excluded_rejects_non_git_directory(self):
        with TemporaryDirectory() as temp:
            plain = Path(temp) / "not-a-repo"
            plain.mkdir()
            with self.assertRaises(WorktreeError):
                ensure_excluded(plain)

    def test_has_uncommitted_changes_reflects_worktree_state(self):
        with TemporaryDirectory() as temp:
            repository = _init_repository(Path(temp))
            worktree = ensure_worktree(repository, "task-1")
            self.assertFalse(has_uncommitted_changes(worktree))
            (worktree / "new-file.txt").write_text("x\n", encoding="utf-8")
            self.assertTrue(has_uncommitted_changes(worktree))

    def test_commit_worktree_changes_commits_and_reports_true(self):
        with TemporaryDirectory() as temp:
            repository = _init_repository(Path(temp))
            worktree = ensure_worktree(repository, "task-1")
            (worktree / "new-file.txt").write_text("x\n", encoding="utf-8")
            committed = commit_worktree_changes(worktree, "agent changes")
            self.assertTrue(committed)
            self.assertFalse(has_uncommitted_changes(worktree))
            self.assertTrue(has_commits_ahead(repository, "task-1"))

    def test_commit_worktree_changes_is_false_when_nothing_to_commit(self):
        with TemporaryDirectory() as temp:
            repository = _init_repository(Path(temp))
            worktree = ensure_worktree(repository, "task-1")
            self.assertFalse(commit_worktree_changes(worktree, "nothing"))
            self.assertFalse(has_commits_ahead(repository, "task-1"))

    def test_has_commits_ahead_false_for_unknown_task(self):
        with TemporaryDirectory() as temp:
            repository = _init_repository(Path(temp))
            self.assertFalse(has_commits_ahead(repository, "never-existed"))

    def test_finalize_worktree_removes_when_no_diff(self):
        with TemporaryDirectory() as temp:
            repository = _init_repository(Path(temp))
            path = ensure_worktree(repository, "task-1")
            kept = finalize_worktree(repository, "task-1", "run finished")
            self.assertFalse(kept)
            self.assertFalse(path.is_dir())
            branches = _git(repository, "branch", "--list", worktree_branch("task-1")).stdout
            self.assertNotIn(worktree_branch("task-1"), branches)

    def test_finalize_worktree_commits_and_keeps_when_there_is_a_diff(self):
        with TemporaryDirectory() as temp:
            repository = _init_repository(Path(temp))
            worktree = ensure_worktree(repository, "task-1")
            (worktree / "new-file.txt").write_text("x\n", encoding="utf-8")
            kept = finalize_worktree(repository, "task-1", "agent changes")
            self.assertTrue(kept)
            self.assertTrue(worktree.is_dir())
            self.assertFalse(has_uncommitted_changes(worktree))

    def test_finalize_worktree_on_missing_worktree_is_a_no_op(self):
        with TemporaryDirectory() as temp:
            repository = _init_repository(Path(temp))
            self.assertFalse(finalize_worktree(repository, "never-created", "n/a"))

    def test_diff_summary_none_when_no_commits_ahead(self):
        with TemporaryDirectory() as temp:
            repository = _init_repository(Path(temp))
            ensure_worktree(repository, "task-1")
            self.assertIsNone(diff_summary(repository, "task-1"))

    def test_diff_summary_reports_changed_files_once_committed(self):
        with TemporaryDirectory() as temp:
            repository = _init_repository(Path(temp))
            worktree = ensure_worktree(repository, "task-1")
            (worktree / "new-file.txt").write_text("x\n", encoding="utf-8")
            commit_worktree_changes(worktree, "agent changes")
            summary = diff_summary(repository, "task-1")
            self.assertIsNotNone(summary)
            self.assertIn("new-file.txt", summary)

    def test_merge_worktree_brings_changes_into_repository_and_cleans_up(self):
        with TemporaryDirectory() as temp:
            repository = _init_repository(Path(temp))
            worktree = ensure_worktree(repository, "task-1")
            (worktree / "new-file.txt").write_text("x\n", encoding="utf-8")
            commit_worktree_changes(worktree, "agent changes")
            merge_worktree(repository, "task-1")
            self.assertTrue((repository / "new-file.txt").is_file())
            self.assertFalse(worktree_path(repository, "task-1").is_dir())
            branches = _git(repository, "branch", "--list", worktree_branch("task-1")).stdout
            self.assertNotIn(worktree_branch("task-1"), branches)

    def test_merge_worktree_raises_for_unknown_task(self):
        with TemporaryDirectory() as temp:
            repository = _init_repository(Path(temp))
            with self.assertRaises(WorktreeError):
                merge_worktree(repository, "never-existed")


if __name__ == "__main__":
    unittest.main()
