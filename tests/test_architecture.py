from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.architecture import check_architecture, generate_architecture_profile
from esc_exec.contracts import validate_contract
from esc_exec.indexing import generate_indexes
from esc_exec.manifests import (
    component_manifest_path, component_manifest_relative_path, repository_manifest_path,
)
from esc_exec.model import ManifestState
from esc_exec.yaml_io import load_yaml, write_yaml


class ArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        # The component's real source tree (root/content/) is deliberately distinct
        # from where its manifest bundle is stored (.esc-ai/components/content/) --
        # this is what makes this fixture a real regression test for the
        # component_root resolution fix in esc_exec.architecture: if component_root
        # were still resolved as manifest_path.parent, rule checking would never
        # find this source tree at all.
        source = self.root / "content/src/main/kotlin"
        source.mkdir(parents=True)
        (source / "Allowed.kt").write_text("import com.example.core.Api\n", encoding="utf-8")
        (source / "Forbidden.kt").write_text(
            "package content\nimport com.example.portal.Secret\nimport com.example.portal.Other\n",
            encoding="utf-8",
        )
        write_yaml(repository_manifest_path(self.root), {
            "schema_version": 1,
            "repository": {"id": "repo", "type": "gradle-multi-project", "purpose": "test"},
            "components": [{"id": "content", "manifest": component_manifest_relative_path("content")}],
        })
        write_yaml(component_manifest_path(self.root, "content"), {
            "schema_version": 1,
            "component": {"id": "content", "type": "gradle-module", "path": "content", "purpose": "content"},
            "build": {"system": "gradle", "project": ":content"},
            "paths": {"source": "src/main/kotlin"},
        })
        generate_indexes(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_reports_stable_bounded_rule_violations(self):
        profile_path = generate_architecture_profile(self.root, "content")
        profile = load_yaml(profile_path)
        profile["limits"] = {"max_violations_per_rule": 1, "max_evidence_chars": 30}
        profile["rules"] = [{
            "id": "content.no-portal-imports",
            "type": "forbidden-import",
            "description": "Content must not depend on portal.",
            "source_root": "src/main/kotlin",
            "patterns": ["com.example.portal.*"],
        }]
        write_yaml(profile_path, profile)
        generate_indexes(self.root)
        output = self.root / "architecture-report.json"
        report = check_architecture(self.root, "content", output)
        self.assertEqual("failed", report["status"])
        self.assertEqual("content.no-portal-imports", report["results"][0]["rule_id"])
        self.assertEqual(1, len(report["results"][0]["violations"]))
        self.assertEqual(1, report["results"][0]["violations_omitted"])
        self.assertEqual(ManifestState.VALID, validate_contract("architecture-report", output).state)

    def test_passes_required_and_forbidden_path_rules(self):
        profile_path = generate_architecture_profile(self.root, "content")
        profile = load_yaml(profile_path)
        profile["rules"] = [
            {"id": "content.source-required", "type": "required-path", "description": "Source exists.", "paths": ["src/main/kotlin"]},
            {"id": "content.no-legacy", "type": "forbidden-path", "description": "No legacy source.", "patterns": ["src/**/legacy/**"]},
        ]
        write_yaml(profile_path, profile)
        report = check_architecture(self.root, "content", self.root / "report.json")
        self.assertEqual("passed", report["status"])


class TypeScriptArchitectureTests(unittest.TestCase):
    """forbidden-import over a Next.js-shaped component: aliases, features, tests, and failing closed."""

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.web = self.root / "web"
        for folder in (
            "src/features/feeds/components", "src/features/feeds/hooks", "src/features/feeds/domain",
            "src/features/feeds/data", "src/features/regions/hooks", "src/app", "src/composition",
        ):
            (self.web / folder).mkdir(parents=True)
        (self.web / "package.json").write_text('{"name": "web", "dependencies": {"firebase": "13.0.0"}}', encoding="utf-8")
        (self.web / "tsconfig.json").write_text(
            '{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}', encoding="utf-8",
        )
        write_yaml(repository_manifest_path(self.root), {
            "schema_version": 1,
            "repository": {"id": "repo", "type": "npm-package", "purpose": "test"},
            "components": [{"id": "web", "manifest": component_manifest_relative_path("web")}],
        })
        write_yaml(component_manifest_path(self.root, "web"), {
            "schema_version": 1,
            "component": {"id": "web", "type": "npm-package", "path": "web", "purpose": "web"},
            "build": {"system": "npm"},
            "paths": {"source": "src"},
        })
        generate_indexes(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def _write(self, relative: str, text: str) -> None:
        path = self.web / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _check(self, rules, pattern_sets=None):
        profile_path = generate_architecture_profile(self.root, "web")
        profile = load_yaml(profile_path)
        profile["rules"] = rules
        if pattern_sets:
            profile["pattern_sets"] = pattern_sets
        write_yaml(profile_path, profile)
        generate_indexes(self.root)
        output = self.root / "report.json"
        report = check_architecture(self.root, "web", output)
        self.assertEqual(ManifestState.VALID, validate_contract("architecture-report", output).state)
        return report

    @staticmethod
    def _rule(**overrides):
        rule = {
            "id": "web.domain-pure", "type": "forbidden-import", "description": "Domain is pure.",
            "source_roots": ["src/features/*/domain"], "patterns": ["react", "firebase/**", "**/hooks/**"],
        }
        rule.update(overrides)
        return rule

    def test_flags_a_package_import_and_a_relative_import_into_another_layer(self):
        self._write("src/features/feeds/domain/rate.ts", "import { useState } from 'react'\nimport { x } from '../hooks/useFeeds'\n")
        report = self._check([self._rule()])
        result = report["results"][0]
        self.assertEqual("failed", result["status"])
        self.assertEqual([1, 2], [violation["line"] for violation in result["violations"]])
        self.assertEqual(1, result["files_scanned"])

    def test_resolves_a_tsconfig_alias_before_matching(self):
        self._write("src/features/feeds/domain/rate.ts", "import { x } from '@/features/feeds/hooks/useFeeds'\n")
        report = self._check([self._rule()])
        self.assertEqual("failed", report["status"])

    def test_a_clean_domain_passes(self):
        self._write("src/features/feeds/domain/rate.ts", "export const rate = (a: number, b: number) => a / b\n")
        self.assertEqual("passed", self._check([self._rule()])["status"])

    def test_test_files_are_not_scanned_unless_asked(self):
        self._write("src/features/feeds/domain/rate.ts", "export const rate = 1\n")
        self._write("src/features/feeds/domain/rate.test.ts", "import 'react'\n")
        self.assertEqual("passed", self._check([self._rule()])["status"])
        self.assertEqual("failed", self._check_again(self._rule(include_tests=True))["status"])

    def _check_again(self, rule):
        profile_path = self.root / ".esc-ai/components/web/esc-architecture-profile.yaml"
        profile = load_yaml(profile_path)
        profile["rules"] = [rule]
        write_yaml(profile_path, profile)
        generate_indexes(self.root)
        return check_architecture(self.root, "web", self.root / "report2.json")

    def test_exclude_skips_matching_files(self):
        self._write("src/features/feeds/domain/FeedsPage.tsx", "import 'react'\n")
        self._write("src/features/feeds/domain/rate.ts", "export const rate = 1\n")
        report = self._check([self._rule(exclude=["**/*Page.tsx"])])
        self.assertEqual("passed", report["status"])
        self.assertEqual(1, report["results"][0]["files_scanned"])

    def test_except_own_feature_allows_internals_of_the_same_feature_only(self):
        self._write("src/features/feeds/components/Feeds.tsx", "import { useFeeds } from '@/features/feeds/hooks/useFeeds'\n")
        self._write("src/features/regions/hooks/useRegions.ts", "import { useFeeds } from '@/features/feeds/hooks/useFeeds'\n")
        rule = {
            "id": "web.through-index", "type": "forbidden-import", "description": "Features are imported through their index.",
            "source_roots": ["src/features/*"], "patterns": ["{internals}"], "except_own_feature": True,
        }
        report = self._check([rule], {"internals": ["**/features/*/hooks/**", "**/features/*/data/**"]})
        violations = report["results"][0]["violations"]
        self.assertEqual(["src/features/regions/hooks/useRegions.ts"], [v["path"] for v in violations])

    def test_a_rule_that_scans_no_files_fails_instead_of_passing(self):
        (self.web / "src/features/feeds/domain/README.md").write_text("no code here\n", encoding="utf-8")
        report = self._check([self._rule()])
        self.assertEqual("failed", report["status"])
        self.assertIn("scanned no source files", report["results"][0]["violations"][0]["evidence"])

    def test_a_missing_source_root_is_an_error_unless_the_rule_is_optional(self):
        self._write("src/features/feeds/domain/rate.ts", "export const rate = 1\n")
        with self.assertRaisesRegex(ValueError, "existing source_root"):
            self._check([self._rule(id="web.missing", source_roots=["src/features/*/useCases"])])
        report = self._check_again(self._rule(id="web.missing", source_roots=["src/features/*/useCases"], optional=True))
        self.assertEqual("passed", report["status"])
        self.assertEqual(0, report["results"][0]["files_scanned"])

    def test_unknown_pattern_set_is_an_error(self):
        self._write("src/features/feeds/domain/rate.ts", "export const rate = 1\n")
        with self.assertRaisesRegex(ValueError, "pattern set"):
            self._check([self._rule(patterns=["{nope}"])])

    def test_node_modules_are_never_scanned(self):
        self._write("src/features/feeds/domain/rate.ts", "export const rate = 1\n")
        self._write("src/features/feeds/domain/node_modules/pkg/index.js", "require('react')\n")
        report = self._check([self._rule()])
        self.assertEqual("passed", report["status"])
        self.assertEqual(1, report["results"][0]["files_scanned"])

    def test_web_app_template_passes_a_clean_layout_and_fails_a_violation(self):
        self._write("src/features/feeds/domain/rate.ts", "export const rate = 1\n")
        self._write("src/features/feeds/hooks/useFeeds.ts", "export const useFeeds = () => 1\n")
        self._write("src/features/feeds/components/FeedsView.tsx", "import { useFeeds } from '../hooks/useFeeds'\n")
        self._write("src/features/feeds/data/FirestoreFeedDataSource.ts", "import { getFirestore } from 'firebase/firestore'\n")
        self._write("src/features/feeds/index.ts", "export {}\n")
        self._write("src/app/page.tsx", "import '@/features/feeds'\n")
        profile_path = generate_architecture_profile(self.root, "web", template="web-app")
        generate_indexes(self.root)
        report = check_architecture(self.root, "web", self.root / "clean.json")
        self.assertEqual("passed", report["status"], report["results"])
        self.assertIn("firebase/**", load_yaml(profile_path)["pattern_sets"]["provider-sdks"])

        self._write("src/features/feeds/components/FeedsView.tsx", "import { getFirestore } from 'firebase/firestore'\n")
        failed = check_architecture(self.root, "web", self.root / "dirty.json")
        self.assertEqual("failed", failed["status"])
        self.assertIn(
            "web.sdk-only-in-datasources",
            [result["rule_id"] for result in failed["results"] if result["status"] == "failed"],
        )

    def test_web_app_template_keeps_shared_datasource_code_out_of_other_layers(self):
        self._write("src/features/feeds/domain/rate.ts", "export const rate = 1\n")
        self._write("src/features/feeds/hooks/useFeeds.ts", "export const useFeeds = () => 1\n")
        self._write("src/features/feeds/components/FeedsView.tsx", "export default function V() { return null }\n")
        self._write("src/features/feeds/data/FirestoreFeedDataSource.ts", "import { toAppError } from '@/data/firestoreErrors'\n")
        self._write("src/data/firestoreErrors.ts", "import { FirebaseError } from 'firebase/app'\nexport const toAppError = (e: unknown) => e instanceof FirebaseError\n")
        self._write("src/features/feeds/index.ts", "export {}\n")
        self._write("src/app/page.tsx", "import '@/features/feeds'\n")
        generate_architecture_profile(self.root, "web", template="web-app")
        generate_indexes(self.root)
        clean = check_architecture(self.root, "web", self.root / "clean.json")
        self.assertEqual("passed", clean["status"], clean["results"])

        self._write("src/features/feeds/hooks/useFeeds.ts", "import { toAppError } from '@/data/firestoreErrors'\n")
        failed = check_architecture(self.root, "web", self.root / "dirty.json")
        self.assertIn(
            "web.shared-data-only-for-datasources",
            [r["rule_id"] for r in failed["results"] if r["status"] == "failed"],
        )

    def test_web_app_template_fails_when_the_layout_is_not_the_assumed_one(self):
        import shutil
        shutil.rmtree(self.web / "src")
        (self.web / "src").mkdir()
        (self.web / "src/other.ts").write_text("export {}\n", encoding="utf-8")
        generate_architecture_profile(self.root, "web", template="web-app")
        generate_indexes(self.root)
        report = check_architecture(self.root, "web", self.root / "wrong.json")
        self.assertEqual("failed", report["status"])
        self.assertIn("web.layout-present", [r["rule_id"] for r in report["results"] if r["status"] == "failed"])

    def test_a_template_fills_an_empty_profile_but_never_overwrites_one_with_rules(self):
        generate_architecture_profile(self.root, "web")          # what onboarding writes: no rules
        profile_path = generate_architecture_profile(self.root, "web", template="web-app")
        self.assertTrue(load_yaml(profile_path)["rules"])
        with self.assertRaisesRegex(ValueError, "already exists"):
            generate_architecture_profile(self.root, "web", template="web-app")

    def test_unknown_template_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "unknown architecture template"):
            generate_architecture_profile(self.root, "web", template="nope")
