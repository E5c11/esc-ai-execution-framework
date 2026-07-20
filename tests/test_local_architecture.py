from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.local_architecture import (
    list_local_architecture_notes, local_architecture_note_path,
    read_local_architecture_note_frontmatter, write_local_architecture_note,
)


class WriteLocalArchitectureNoteTests(unittest.TestCase):
    def test_writes_frontmatter_and_body(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            path = write_local_architecture_note(
                root, "background-jobs",
                doc_id="LOCAL-BACKGROUND-JOBS", doc_type="pattern", layer="pattern",
                platform=["backend"], architecture=["all"],
                title="Background Job Scheduling", body="Use a queue-backed worker.",
                requires=["CORE-DI"], related=["PAT-OUTCOME"], tags=["jobs", "async"],
            )
            self.assertEqual(local_architecture_note_path(root, "background-jobs"), path)
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\n"))
            self.assertIn("id: LOCAL-BACKGROUND-JOBS", text)
            self.assertIn("status: stub", text)
            self.assertIn("# Background Job Scheduling", text)
            self.assertIn("Use a queue-backed worker.", text)

    def test_status_is_always_stub_regardless_of_input(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            path = write_local_architecture_note(
                root, "x", doc_id="LOCAL-X", doc_type="pattern", layer="pattern",
                platform=["all"], architecture=["all"], title="X", body="body",
            )
            frontmatter = read_local_architecture_note_frontmatter(path)
            self.assertEqual("stub", frontmatter["status"])

    def test_optional_fields_default_to_empty_lists(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            path = write_local_architecture_note(
                root, "x", doc_id="LOCAL-X", doc_type="pattern", layer="pattern",
                platform=["all"], architecture=["all"], title="X", body="body",
            )
            frontmatter = read_local_architecture_note_frontmatter(path)
            self.assertEqual([], frontmatter["requires"])
            self.assertEqual([], frontmatter["related"])
            self.assertEqual([], frontmatter["tags"])

    def test_creates_parent_directory(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            self.assertFalse((root / ".esc-ai" / "local-architecture").exists())
            write_local_architecture_note(
                root, "x", doc_id="LOCAL-X", doc_type="pattern", layer="pattern",
                platform=["all"], architecture=["all"], title="X", body="body",
            )
            self.assertTrue((root / ".esc-ai" / "local-architecture").is_dir())


class ReadLocalArchitectureNoteFrontmatterTests(unittest.TestCase):
    def test_missing_file_returns_empty_dict(self):
        with TemporaryDirectory() as name:
            self.assertEqual({}, read_local_architecture_note_frontmatter(Path(name) / "missing.md"))

    def test_file_without_frontmatter_delimiter_returns_empty_dict(self):
        with TemporaryDirectory() as name:
            path = Path(name) / "plain.md"
            path.write_text("# Just a heading\n\nNo frontmatter here.\n", encoding="utf-8")
            self.assertEqual({}, read_local_architecture_note_frontmatter(path))

    def test_unterminated_frontmatter_returns_empty_dict(self):
        with TemporaryDirectory() as name:
            path = Path(name) / "broken.md"
            path.write_text("---\nid: X\n", encoding="utf-8")
            self.assertEqual({}, read_local_architecture_note_frontmatter(path))


class ListLocalArchitectureNotesTests(unittest.TestCase):
    def test_empty_when_directory_does_not_exist(self):
        with TemporaryDirectory() as name:
            self.assertEqual([], list_local_architecture_notes(Path(name)))

    def test_lists_every_note_sorted(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            write_local_architecture_note(
                root, "z-note", doc_id="LOCAL-Z", doc_type="pattern", layer="pattern",
                platform=["all"], architecture=["all"], title="Z", body="body",
            )
            write_local_architecture_note(
                root, "a-note", doc_id="LOCAL-A", doc_type="pattern", layer="pattern",
                platform=["all"], architecture=["all"], title="A", body="body",
            )
            notes = list_local_architecture_notes(root)
            self.assertEqual(["a-note.md", "z-note.md"], [path.name for path in notes])


if __name__ == "__main__":
    unittest.main()
