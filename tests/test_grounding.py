"""The `grounding_check` stage of the document procedure: only documentation may change, and the references in it
must resolve. Most of this file is about what is NOT a reference, because false positives are what would teach
people to ignore the gate."""
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from esc_exec.grounding import (
    count_references,
    extract_references,
    grounding_blockers,
    is_documentation,
    non_documentation_changes,
)
from esc_exec.procedures import GROUNDING_CHECK
from esc_exec.read_only import changed_paths
from esc_exec.worktree import ensure_worktree, finalize_worktree, repository_state, worktree_changed_paths

REPO = {"src", "src/export", "src/export/Csv.kt", "docs", "docs/guide.md", "README.md", "build.gradle.kts"}


def exists(path: str) -> bool:
    return path in REPO


def blockers(text: str, document: str = "docs/new.md") -> list[str]:
    return grounding_blockers({document: text}, exists)


class DocumentationClassificationTests(unittest.TestCase):
    def test_documentation_is_by_suffix_or_the_docs_directory(self):
        for path in ("README.md", "a/b/guide.MD", "notes.rst", "x.txt", "docs/diagram.png", "doc/api.yaml", "a.mdx", "b.adoc"):
            self.assertTrue(is_documentation(path), path)
        for path in ("src/Csv.kt", "build.gradle.kts", "tests/test_x.py", "docsy/x.png", ".github/workflows/ci.yml"):
            self.assertFalse(is_documentation(path), path)

    def test_non_documentation_changes_lists_code_and_ignores_escape_ais_own_directories(self):
        changed = ["README.md", "src/Csv.kt", "docs/a.md", ".esc-ai/runs/r/summary.json", ".esc-ai/worktrees/t/x", "b.py"]
        self.assertEqual(["b.py", "src/Csv.kt"], non_documentation_changes(changed))


class ReferenceExtractionTests(unittest.TestCase):
    def paths(self, text):
        return [(r.path, r.line, r.explicit) for r in extract_references(text)]

    def test_inline_code_paths_and_links_are_extracted_with_their_line(self):
        text = "Intro\nSee `src/export/Csv.kt:42` and [the guide](docs/guide.md#usage).\n"
        self.assertEqual([("docs/guide.md", 2, True), ("src/export/Csv.kt", 2, False)], self.paths(text))

    def test_line_ranges_and_anchors_are_stripped(self):
        found = [reference.path for reference in extract_references("`src/a/B.kt:10-20`\n[x](docs/x.md#top)")]
        self.assertEqual(["src/a/B.kt", "docs/x.md"], found)

    def test_things_that_are_not_paths_are_not_extracted(self):
        for text in (
            "`npm run build`", "`a/b*.kt`", "`src/<name>/x.kt`", "`$HOME/x/y`", "`{a,b}/c`",
            "`/etc/hosts`", "`~/dotfiles/x`", "[site](https://example.com/a/b.md)", "[mail](mailto:a@b.co)", "[top](#section)",
            "```\nsrc/inside/fence.kt\n```", "~~~\n`src/also/fenced.kt`\n~~~",
        ):
            with self.subTest(text=text):
                self.assertEqual([], self.paths(text), text)

    def test_extraction_is_generous_and_the_claim_filter_is_what_keeps_prose_out(self):
        # `read/write` is extracted (it contains a slash) but is not a claim about a file: see GroundingTests.
        self.assertEqual([("read/write", 1, False)], self.paths("`read/write`"))
        self.assertEqual(0, count_references({"d.md": "`read/write` over `TCP/IP`"}, exists))

    def test_text_after_a_closed_fence_is_read_again(self):
        self.assertEqual([("src/after.kt", 4, False)], self.paths("```\nx\n```\n`src/after.kt`\n"))


class GroundingTests(unittest.TestCase):
    def test_real_references_resolve(self):
        self.assertEqual([], blockers("Uses `src/export/Csv.kt` and `docs/guide.md`, see [readme](README.md) and `src/export/`."))

    def test_a_missing_file_is_named_with_document_and_line(self):
        self.assertEqual(
            ["docs/new.md:2: `src/export/Excel.kt` does not exist in the repository"],
            blockers("Intro\nExport lives in `src/export/Excel.kt`.\n"),
        )

    def test_a_missing_directory_and_a_missing_file_inside_a_real_directory_are_both_caught(self):
        self.assertEqual(2, len(blockers("`src/gone/` and `src/export/Missing`")))

    def test_a_broken_markdown_link_is_always_a_claim(self):
        self.assertEqual(["docs/new.md:1: `docs/nope.md` does not exist in the repository"], blockers("[x](docs/nope.md)"))

    def test_a_link_relative_to_the_document_resolves(self):
        self.assertEqual([], blockers("[guide](guide.md)", document="docs/other.md"))
        self.assertEqual(1, len(blockers("[guide](guide.md)", document="src/other.md")))

    def test_a_link_cannot_escape_the_repository(self):
        self.assertEqual(1, len(blockers("[x](../../etc/passwd)")))

    def test_prose_that_only_looks_like_a_path_is_not_a_false_positive(self):
        for text in (
            "Reads and writes via `read/write` calls over `TCP/IP`.", "`client/server` model", "run `git checkout feature/x`",
            "see `github.com/org/repo`", "the `1/2` split", "`and/or`",
        ):
            with self.subTest(text=text):
                self.assertEqual([], blockers(text), text)

    def test_a_hostname_with_a_file_extension_is_not_a_missing_file(self):
        self.assertEqual([], blockers("Docs at `docs.example.com/index.html`."))

    def test_a_path_with_an_extension_is_a_claim_even_if_its_top_directory_is_missing(self):
        self.assertEqual(1, len(blockers("`nothing/here/File.kt`")))

    def test_the_same_reference_twice_on_a_line_is_reported_once(self):
        self.assertEqual(1, len(blockers("`src/x/Gone.kt` and again `src/x/Gone.kt`")))

    def test_every_document_is_checked_and_reported_in_order(self):
        result = grounding_blockers({"b.md": "`src/nope/A.kt`", "a.md": "`src/nope/B.kt`"}, exists)
        self.assertEqual(["a.md", "b.md"], [line.split(":")[0] for line in result])

    def test_count_references_counts_only_claims(self):
        text = "`src/export/Csv.kt` `read/write` [g](docs/guide.md)"
        self.assertEqual(2, count_references({"d.md": text}, exists))

    def test_a_document_with_no_references_has_no_blockers(self):
        self.assertEqual([], blockers("Just prose. Nothing to cite.\n"))


class ChangedPathsTests(unittest.TestCase):
    def snap(self, **files):
        return {"head": "a" * 40, "files": files}

    def test_new_and_changed_paths_are_reported_and_prior_work_is_not(self):
        before = self.snap(wip="M:1", same="M:2")
        after = self.snap(wip="M:1", same="M:9", new="??:3", **{".esc-ai/runs/r/x": "??:4"})
        self.assertEqual(["new", "same"], changed_paths(before, after))

    def test_missing_snapshots_yield_nothing(self):
        self.assertEqual([], changed_paths(None, self.snap()))
        self.assertEqual([], changed_paths(self.snap(), None))


def git(repository: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repository), *args], capture_output=True, text=True, check=True)


class WorktreeChangedPathsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "T")
        (self.repo / "a.md").write_text("one\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")

    def tearDown(self):
        self.temp.cleanup()

    def test_no_branch_means_no_changes(self):
        self.assertEqual([], worktree_changed_paths(self.repo, "t"))

    def test_the_branch_diff_lists_added_changed_and_deleted_paths(self):
        worktree = ensure_worktree(self.repo, "t")
        (worktree / "a.md").write_text("two\n")
        (worktree / "docs").mkdir()
        (worktree / "docs" / "new.md").write_text("new\n")
        (worktree / "src.py").write_text("x = 1\n")
        finalize_worktree(self.repo, "t", "docs")
        self.assertEqual(["a.md", "docs/new.md", "src.py"], sorted(worktree_changed_paths(self.repo, "t")))

    def test_the_live_checkout_snapshot_is_what_a_live_editing_run_is_compared_by(self):
        before = repository_state(self.repo)
        (self.repo / "a.md").write_text("changed\n")
        self.assertEqual(["a.md"], changed_paths(before, repository_state(self.repo)))


class ProcedureClaimTests(unittest.TestCase):
    def test_grounding_check_is_no_longer_declared_new_and_states_its_limit(self):
        self.assertFalse(GROUNDING_CHECK.maps_to.startswith("new"))
        self.assertIn("esc_exec.grounding", GROUNDING_CHECK.maps_to)
        self.assertIn("not that the prose is accurate", GROUNDING_CHECK.maps_to)
        self.assertEqual("gate", GROUNDING_CHECK.kind)


if __name__ == "__main__":
    unittest.main()
