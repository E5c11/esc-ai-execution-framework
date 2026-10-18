from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.ts_imports import (
    glob_matches, is_test_file, load_tsconfig_paths, module_specifiers, resolve_import, strip_comments,
)


class StripCommentsTests(unittest.TestCase):
    def test_keeps_line_numbers(self):
        source = "a\n// gone\nb /* x\ny */ c\nd\n"
        stripped = strip_comments(source)
        self.assertEqual(source.count("\n"), stripped.count("\n"))
        self.assertNotIn("gone", stripped)

    def test_comment_markers_inside_strings_are_not_comments(self):
        self.assertIn("http://x", strip_comments("const u = 'http://x'; // c"))

    def test_apostrophe_in_jsx_text_does_not_swallow_the_file(self):
        source = "<p>Don't</p>\nimport x from 'real'\n"
        self.assertIn("real", strip_comments(source))


class ModuleSpecifierTests(unittest.TestCase):
    def specifiers(self, source):
        return [spec for _, spec in module_specifiers(source)]

    def test_finds_every_static_form(self):
        source = (
            "import a from 'a'\n"
            "import type { B } from \"b\"\n"
            "import {\n  c,\n  d,\n} from 'c'\n"
            "import './side-effect.css'\n"
            "export * from 'e'\n"
            "export { f } from 'f'\n"
        )
        self.assertEqual(
            ["./side-effect.css", "a", "b", "c", "e", "f"], sorted(self.specifiers(source)),
        )

    def test_finds_dynamic_import_and_require(self):
        self.assertEqual(["x", "y"], sorted(self.specifiers("const m = await import('x'); const n = require('y')")))

    def test_ignores_commented_imports_and_array_from(self):
        source = "// import a from 'a'\n/* import b from 'b' */\nconst x = Array.from('abc')\n"
        self.assertEqual([], self.specifiers(source))

    def test_reports_the_line_of_a_multi_line_import_at_its_specifier(self):
        found = module_specifiers("import {\n  a,\n} from 'pkg'\n")
        self.assertEqual([(3, "pkg")], found)

    def test_ignores_a_dynamic_import_with_a_computed_argument(self):
        self.assertEqual([], self.specifiers("import(`./${name}`)"))


class GlobTests(unittest.TestCase):
    def test_double_star_crosses_directories_and_single_star_does_not(self):
        self.assertTrue(glob_matches("features/*/data/**", "features/a/data/FirestoreX"))
        self.assertFalse(glob_matches("features/*/data/**", "features/a/b/data/X"))
        self.assertTrue(glob_matches("**/data/Firestore*", "src/features/a/data/FirestoreFeeds"))
        self.assertFalse(glob_matches("**/data/Firestore*", "src/features/a/metadata/FirestoreFeeds"))

    def test_package_specifiers(self):
        self.assertTrue(glob_matches("firebase/**", "firebase/firestore"))
        self.assertTrue(glob_matches("firebase", "firebase"))
        self.assertFalse(glob_matches("firebase/**", "firebase-admin"))


class TestFileTests(unittest.TestCase):
    def test_recognises_test_files(self):
        for name in ("a.test.ts", "a.spec.tsx", "FeedDataSource.contract.ts", "__tests__/a.ts"):
            self.assertTrue(is_test_file(Path("src") / name), name)
        self.assertFalse(is_test_file(Path("src/a.ts")))


class TsconfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self):
        self.temporary.cleanup()

    def test_no_tsconfig_means_no_aliases(self):
        paths = load_tsconfig_paths(self.root)
        self.assertEqual((), paths.aliases)
        self.assertIsNone(resolve_import("@/x", self.root / "a.ts", self.root, paths))

    def test_reads_jsonc_with_comments_and_trailing_commas(self):
        (self.root / "tsconfig.json").write_text(
            '{\n  // alias\n  "compilerOptions": { "paths": { "@/*": ["./src/*"], }, },\n}\n', encoding="utf-8",
        )
        paths = load_tsconfig_paths(self.root)
        self.assertEqual("src/features/a/data/X", resolve_import("@/features/a/data/X", self.root / "src/a.ts", self.root, paths))

    def test_follows_relative_extends(self):
        (self.root / "tsconfig.base.json").write_text(
            '{"compilerOptions": {"baseUrl": ".", "paths": {"~/*": ["src/*"]}}}', encoding="utf-8",
        )
        (self.root / "tsconfig.json").write_text('{"extends": "./tsconfig.base"}', encoding="utf-8")
        paths = load_tsconfig_paths(self.root)
        self.assertEqual("src/x", resolve_import("~/x", self.root / "a.ts", self.root, paths))

    def test_base_url_resolves_a_bare_specifier_that_is_a_local_module(self):
        (self.root / "tsconfig.json").write_text('{"compilerOptions": {"baseUrl": "src"}}', encoding="utf-8")
        (self.root / "src/features").mkdir(parents=True)
        paths = load_tsconfig_paths(self.root)
        self.assertEqual("src/features", resolve_import("features", self.root / "a.ts", self.root, paths))
        self.assertIsNone(resolve_import("react", self.root / "a.ts", self.root, paths))

    def test_relative_import_resolves_against_the_importer_and_drops_the_extension(self):
        paths = load_tsconfig_paths(self.root)
        importer = self.root / "src/features/a/hooks/useA.ts"
        self.assertEqual("src/features/a/data/X", resolve_import("../data/X.ts", importer, self.root, paths))

    def test_package_specifier_is_not_local(self):
        self.assertIsNone(resolve_import("firebase/firestore", self.root / "a.ts", self.root, load_tsconfig_paths(self.root)))


if __name__ == "__main__":
    unittest.main()
