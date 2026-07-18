from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.adapters import GradleAdapter, detect_build_system


class AdaptersTests(unittest.TestCase):
    def test_detects_gradle_repository(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "settings.gradle.kts").write_text(
                'rootProject.name = "sample"\ninclude(":content")\n', encoding="utf-8",
            )
            (root / "content").mkdir()
            repository_id, components, adapter = detect_build_system(root)
            self.assertEqual("sample", repository_id)
            self.assertEqual([("content", Path("content"))], components)
            self.assertIsInstance(adapter, GradleAdapter)
            self.assertEqual("gradle", adapter.name)

    def test_raises_when_no_adapter_detects_a_build(self):
        with TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "No supported build-system adapter"):
                detect_build_system(Path(temp))


if __name__ == "__main__":
    unittest.main()
