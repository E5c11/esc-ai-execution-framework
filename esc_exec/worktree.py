from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class WorktreeError(RuntimeError):
    pass


# Mirrors the same on-disk shape already proven live (found in ampm-backend
# during the same session this was designed in, created by Claude Code's own
# `Agent` tool for an unrelated task): `.claude/worktrees/agent-<hash>/` on
# branch `worktree-agent-<hash>`. escape-ai's own runs live under `.esc-ai/`
# already (`.esc-ai/runs/<run-id>/`), so worktrees sit alongside that, not under
# `.claude/`, which belongs to a different tool. See
# plan/future/pre-flight-consent-and-bounded-autonomy.md layer 4.
WORKTREES_ROOT = Path(".esc-ai/worktrees")
EXCLUDE_ENTRY = "**/.esc-ai/worktrees/"


def worktree_path(repository: Path, task_id: str) -> Path:
    return repository / WORKTREES_ROOT / task_id


def worktree_branch(task_id: str) -> str:
    return f"esc-ai-task-{task_id}"


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def _branch_exists(repository: Path, branch: str) -> bool:
    return _run(repository, "rev-parse", "--verify", "--quiet", branch).returncode == 0


def ensure_excluded(repository: Path) -> None:
    """
    Local-only exclusion for a task's worktrees, via `.git/info/exclude` -- the
    same file (not `.gitignore`) the `.claude/worktrees/` convention this mirrors
    already uses. A per-clone exclusion, not a change to the repository's own
    tracked files; nothing about adopting worktree isolation requires touching a
    target repository's committed content. Idempotent.
    """
    exclude_file = repository / ".git" / "info" / "exclude"
    if not exclude_file.parent.is_dir():
        raise WorktreeError(f"{repository} is not a git repository (no .git/info directory)")
    existing = exclude_file.read_text(encoding="utf-8") if exclude_file.is_file() else ""
    if EXCLUDE_ENTRY in existing.splitlines():
        return
    with exclude_file.open("a", encoding="utf-8") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        handle.write(EXCLUDE_ENTRY + "\n")


def ensure_worktree(repository: Path, task_id: str) -> Path:
    """
    Create, or reuse across retries of the same task, a disposable git worktree
    for a task run. Idempotent: a second call for the same task_id while the
    worktree still exists on disk just returns its path, same as the first call
    -- a retried task keeps working on the same branch, not a fresh one each
    attempt.
    """
    path = worktree_path(repository, task_id)
    if path.is_dir():
        return path
    ensure_excluded(repository)
    path.parent.mkdir(parents=True, exist_ok=True)
    branch = worktree_branch(task_id)
    command = ["worktree", "add", str(path)]
    command += [branch] if _branch_exists(repository, branch) else ["-b", branch]
    result = _run(repository, *command)
    if result.returncode != 0:
        raise WorktreeError(f"git worktree add failed: {result.stderr.strip()}")
    return path


def copy_inherited_files(repository: Path, worktree: Path, files: list[str]) -> list[str]:
    """
    Copies each repository-root-relative path in `files` from the main checkout
    into a fresh worktree, if the source file exists there -- gitignored local
    config (`local.properties`, `.env`) that a fresh worktree's own git checkout
    never contains (correct git-worktree behavior, but a real footgun for any
    build that depends on it -- see
    plan/active/pre-flight-doctor-and-gate-prerequisites.md). `files` is a plain
    list handed in by the caller (the repository manifest's opt-in
    `worktree_inherit` declaration) -- this function only knows how to copy,
    never how the list was decided. A repository without a given file configured
    yet is not an error; inheritance is opt-in per file, not required. Returns the
    subset that was actually copied, for the caller to log/record.
    """
    copied = []
    for relative in files:
        source = repository / relative
        if not source.is_file():
            continue
        destination = worktree / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(relative)
    return copied


def has_uncommitted_changes(worktree: Path) -> bool:
    return bool(_run(worktree, "status", "--porcelain").stdout.strip())


def commit_worktree_changes(worktree: Path, message: str) -> bool:
    """
    Commits whatever a task run left in the worktree's working tree, if
    anything, and reports whether there was something to commit. A read-only
    task run never produces a commit -- both "does this task have a diff worth
    reviewing" and "merge this back" need to operate on real commits, not
    workdir-only changes that `git worktree remove` would otherwise silently
    discard.
    """
    if not has_uncommitted_changes(worktree):
        return False
    add_result = _run(worktree, "add", "-A")
    if add_result.returncode != 0:
        raise WorktreeError(f"git add failed in worktree: {add_result.stderr.strip()}")
    commit_result = _run(worktree, "commit", "-m", message)
    if commit_result.returncode != 0:
        raise WorktreeError(f"git commit failed in worktree: {commit_result.stderr.strip()}")
    return True


def has_commits_ahead(repository: Path, task_id: str) -> bool:
    branch = worktree_branch(task_id)
    if not _branch_exists(repository, branch):
        return False
    result = _run(repository, "rev-list", "--count", f"HEAD..{branch}")
    return result.returncode == 0 and result.stdout.strip() not in ("", "0")


def remove_worktree(repository: Path, task_id: str, delete_branch: bool = False) -> None:
    path = worktree_path(repository, task_id)
    if path.is_dir():
        result = _run(repository, "worktree", "remove", str(path), "--force")
        if result.returncode != 0:
            raise WorktreeError(f"git worktree remove failed: {result.stderr.strip()}")
    if delete_branch:
        branch = worktree_branch(task_id)
        if _branch_exists(repository, branch):
            _run(repository, "branch", "-D", branch)


def finalize_worktree(repository: Path, task_id: str, commit_message: str) -> bool:
    """
    Call once a run has finished (success or failure alike). Commits anything
    left uncommitted, then removes the worktree immediately if it produced no
    real diff -- a run that never changed anything needs no review step -- or
    keeps it (worktree and branch both) for a human to review via
    `promote-checkpoint` if it did. Returns whether the worktree was kept.
    A missing worktree (never created, or already finalized) is a no-op,
    reported as not-kept.
    """
    worktree = worktree_path(repository, task_id)
    if not worktree.is_dir():
        return False
    commit_worktree_changes(worktree, commit_message)
    if has_commits_ahead(repository, task_id):
        return True
    remove_worktree(repository, task_id, delete_branch=True)
    return False


def diff_summary(repository: Path, task_id: str) -> str | None:
    """`git diff --stat` of a task's worktree branch against the repository's
    current HEAD -- None if the branch doesn't exist or has no real changes.
    Purely informational, for a human review step; nothing enforces against it."""
    branch = worktree_branch(task_id)
    if not _branch_exists(repository, branch):
        return None
    result = _run(repository, "diff", "--stat", f"HEAD...{branch}")
    return result.stdout.strip() or None


def merge_worktree(repository: Path, task_id: str) -> None:
    """
    Merges a task's worktree branch into whatever branch `repository` currently
    has checked out, then removes the worktree and the now-merged branch. The
    "act" half of promote-checkpoint's review step; the "preview" half is
    `diff_summary` above.
    """
    branch = worktree_branch(task_id)
    if not _branch_exists(repository, branch):
        raise WorktreeError(f"no worktree branch found for task `{task_id}`")
    result = _run(repository, "merge", "--no-edit", branch)
    if result.returncode != 0:
        raise WorktreeError(f"git merge failed: {result.stderr.strip()}")
    remove_worktree(repository, task_id, delete_branch=True)
