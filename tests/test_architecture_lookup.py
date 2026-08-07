import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.architecture_lookup import (
    architecture_prompt_lines,
    load_architecture_index,
    load_profile_doc_map,
    resolve_architecture_docs,
    stub_documents,
    suggest_profile_ids,
)


def _doc(doc_id: str, layer: str, requires: list[str] | None = None, status: str = "active") -> dict:
    return {
        "id": doc_id,
        "type": "principle",
        "layer": layer,
        "path": f"{layer}/{doc_id}.md",
        "platform": ["all"],
        "architecture": ["all"],
        "requires": requires or [],
        "related": [],
        "tags": [],
        "status": status,
    }


class ArchitectureLookupTests(unittest.TestCase):
    def test_resolves_requires_chain_before_the_dependent_doc(self):
        index = {
            "CORE-DI": _doc("CORE-DI", "core"),
            "ARCH-BE": _doc("ARCH-BE", "architectures", requires=["CORE-DI"]),
        }
        documents, missing = resolve_architecture_docs(["ARCH-BE"], index)
        self.assertEqual(["CORE-DI", "ARCH-BE"], [document["id"] for document in documents])
        self.assertEqual([], missing)

    def test_merges_multiple_seeds_preserving_layer_order(self):
        index = {
            "CORE-DI": _doc("CORE-DI", "core"),
            "PAT-DATA-ACCESS": _doc("PAT-DATA-ACCESS", "patterns", requires=["CORE-DI"]),
            "ARCH-BE": _doc("ARCH-BE", "architectures", requires=["CORE-DI"]),
            "ORCH-BE-FEAT": _doc("ORCH-BE-FEAT", "feature-orchestrators", requires=["ARCH-BE"]),
        }
        documents, missing = resolve_architecture_docs(["PAT-DATA-ACCESS", "ORCH-BE-FEAT"], index)
        ids = [document["id"] for document in documents]
        self.assertEqual(4, len(ids))
        self.assertEqual([], missing)
        # CORE-DI is shared by both seeds and must appear once, before its dependents.
        self.assertEqual(1, ids.count("CORE-DI"))
        self.assertLess(ids.index("CORE-DI"), ids.index("PAT-DATA-ACCESS"))
        self.assertLess(ids.index("CORE-DI"), ids.index("ARCH-BE"))
        # feature-orchestrators is the highest layer and must sort last.
        self.assertEqual("ORCH-BE-FEAT", ids[-1])

    def test_cycle_does_not_infinite_loop(self):
        index = {
            "A": _doc("A", "core", requires=["B"]),
            "B": _doc("B", "core", requires=["A"]),
        }
        documents, missing = resolve_architecture_docs(["A"], index)
        self.assertEqual({"A", "B"}, {document["id"] for document in documents})
        self.assertEqual([], missing)

    def test_missing_doc_id_is_reported_not_dropped_silently(self):
        index = {"CORE-DI": _doc("CORE-DI", "core")}
        documents, missing = resolve_architecture_docs(["CORE-DI", "ARCH-DOES-NOT-EXIST"], index)
        self.assertEqual(["CORE-DI"], [document["id"] for document in documents])
        self.assertEqual(["ARCH-DOES-NOT-EXIST"], missing)

    def test_stub_documents_are_detected(self):
        index = {
            "CORE-DI": _doc("CORE-DI", "core"),
            "ARCH-STUB": _doc("ARCH-STUB", "architectures", requires=["CORE-DI"], status="stub"),
        }
        documents, _ = resolve_architecture_docs(["ARCH-STUB"], index)
        stubs = stub_documents(documents)
        self.assertEqual(["ARCH-STUB"], [document["id"] for document in stubs])

    def test_documents_with_empty_status_are_treated_as_not_yet_reviewed(self):
        # Regression test: stub_documents() must be a whitelist (only 'active'
        # passes), not a blacklist (only 'stub' blocks). A document that was
        # scaffolded by Tier-1 auto-suggestion but never promoted by a maintainer
        # has status == '' -- not literally 'stub', but just as unreviewed, and
        # must be treated identically. Getting this wrong let every architecture-
        # coverage hard-stop in a real session silently pass on 87 never-reviewed
        # documents (esc-ai-architecture-framework's index at the time: 87/91 at
        # status '', only 1 ever 'active').
        index = {"CORE-UNREVIEWED": _doc("CORE-UNREVIEWED", "core", status="")}
        documents, _ = resolve_architecture_docs(["CORE-UNREVIEWED"], index)
        stubs = stub_documents(documents)
        self.assertEqual(["CORE-UNREVIEWED"], [document["id"] for document in stubs])

    def test_documents_with_any_non_active_status_are_treated_as_not_yet_reviewed(self):
        # Same whitelist point, generalized: 'draft', 'wip', or any other
        # in-progress value a maintainer might type must block exactly like
        # 'stub' -- only the literal 'active' is ever considered reviewed.
        index = {"CORE-DRAFT": _doc("CORE-DRAFT", "core", status="draft")}
        documents, _ = resolve_architecture_docs(["CORE-DRAFT"], index)
        stubs = stub_documents(documents)
        self.assertEqual(["CORE-DRAFT"], [document["id"] for document in stubs])

    def test_active_documents_are_not_flagged(self):
        index = {"CORE-DI": _doc("CORE-DI", "core", status="active")}
        documents, _ = resolve_architecture_docs(["CORE-DI"], index)
        stubs = stub_documents(documents)
        self.assertEqual([], stubs)

    def test_load_architecture_index_reads_a_real_file(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "index.json").write_text(json.dumps({
                "generated": "2026-01-01T00:00:00Z",
                "count": 1,
                "documents": [_doc("CORE-DI", "core")],
            }), encoding="utf-8")
            index = load_architecture_index(root)
            self.assertEqual(["CORE-DI"], list(index))

    def test_load_architecture_index_missing_file_raises(self):
        with TemporaryDirectory() as temp:
            with self.assertRaises(FileNotFoundError):
                load_architecture_index(Path(temp))

    def test_suggest_profile_ids_matches_frameworks_and_targets(self):
        profile_doc_map = {
            "frameworks": {"network": {"ktor": ["PLAT-MOB-HTTP"]}},
            "targets": {"ios": ["PLAT-MOB-KMP-IOS"]},
        }
        suggested = suggest_profile_ids({"network": "ktor"}, ["ios"], profile_doc_map)
        self.assertEqual(["PLAT-MOB-KMP-IOS", "PLAT-MOB-HTTP"], suggested)

    def test_suggest_profile_ids_deduplicates_and_preserves_first_seen_order(self):
        profile_doc_map = {
            "frameworks": {
                "network": {"ktor": ["PLAT-MOB-HTTP"]},
                "cloud": {"firebase": ["PLAT-MOB-HTTP", "PLAT-MOB-FIREBASE"]},
            },
            "targets": {},
        }
        suggested = suggest_profile_ids({"network": "ktor", "cloud": "firebase"}, [], profile_doc_map)
        self.assertEqual(["PLAT-MOB-HTTP", "PLAT-MOB-FIREBASE"], suggested)

    def test_suggest_profile_ids_unmatched_signals_yields_empty(self):
        profile_doc_map = {"frameworks": {"network": {"ktor": ["PLAT-MOB-HTTP"]}}, "targets": {}}
        self.assertEqual([], suggest_profile_ids({"network": "unknown-lib"}, [], profile_doc_map))

    def test_suggest_profile_ids_architecture_style_absent_is_unchanged_regression(self):
        # architecture_style=None (the default) must behave identically to before
        # this parameter existed -- strictly additive, never a change to today's
        # default path (plan/active/npm-architecture-profile-detection.md design
        # section 2).
        profile_doc_map = {"frameworks": {"ui": {"next": ["PLAT-WEB-NEXT"]}}, "targets": {}}
        self.assertEqual(["PLAT-WEB-NEXT"], suggest_profile_ids({"ui": "next"}, [], profile_doc_map))
        self.assertEqual(
            ["PLAT-WEB-NEXT"], suggest_profile_ids({"ui": "next"}, [], profile_doc_map, architecture_style=None),
        )

    def test_suggest_profile_ids_web_app_style_adds_next_app_when_next_resolves(self):
        profile_doc_map = {"frameworks": {"ui": {"next": ["PLAT-WEB-NEXT"]}}, "targets": {}}
        suggested = suggest_profile_ids({"ui": "next"}, [], profile_doc_map, architecture_style="web-app")
        self.assertEqual(["PLAT-WEB-NEXT", "PLAT-WEB-NEXT-APP"], suggested)

    def test_suggest_profile_ids_web_app_style_has_no_effect_without_next_resolving(self):
        # The style flag must never add PLAT-WEB-NEXT-APP unless the generic
        # Next.js doc actually resolved first -- a web-app style answer for a
        # non-Next.js framework signal is a no-op, not an invented addition.
        profile_doc_map = {"frameworks": {"network": {"ktor": ["PLAT-MOB-HTTP"]}}, "targets": {}}
        suggested = suggest_profile_ids({"network": "ktor"}, [], profile_doc_map, architecture_style="web-app")
        self.assertEqual(["PLAT-MOB-HTTP"], suggested)

    def test_suggest_profile_ids_web_content_style_does_not_add_next_app(self):
        # web-content is deliberately not handled (no confirmed doc ID exists yet
        # -- open question 2 in the plan); must not fall back to guessing
        # PLAT-WEB-NEXT-APP or any other ID.
        profile_doc_map = {"frameworks": {"ui": {"next": ["PLAT-WEB-NEXT"]}}, "targets": {}}
        suggested = suggest_profile_ids({"ui": "next"}, [], profile_doc_map, architecture_style="web-content")
        self.assertEqual(["PLAT-WEB-NEXT"], suggested)

    def test_load_profile_doc_map_reads_a_real_file(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "profile-doc-map.json").write_text(json.dumps({
                "frameworks": {"network": {"ktor": ["PLAT-MOB-HTTP"]}},
                "targets": {},
            }), encoding="utf-8")
            data = load_profile_doc_map(root)
            self.assertEqual(["PLAT-MOB-HTTP"], data["frameworks"]["network"]["ktor"])

    def test_load_profile_doc_map_missing_file_raises(self):
        with TemporaryDirectory() as temp:
            with self.assertRaises(FileNotFoundError):
                load_profile_doc_map(Path(temp))

    def test_architecture_prompt_lines_absent_architecture_is_empty(self):
        self.assertEqual([], architecture_prompt_lines({"id": "content"}))

    def test_architecture_prompt_lines_points_at_each_resolved_document(self):
        component = {
            "id": "content",
            "architecture": {
                "profile_ids": ["ORCH-BE-FEAT"],
                "documents": [
                    {"id": "CORE-DI", "path": "core/dependency-inversion.md", "layer": "core"},
                    {"id": "ORCH-BE-FEAT", "path": "feature-orchestrators/backend/feature.md", "layer": "feature-orchestrators"},
                ],
            },
        }
        lines = architecture_prompt_lines(component)
        self.assertEqual(2, len(lines))
        self.assertIn("core/dependency-inversion.md", lines[0])
        self.assertIn("CORE-DI", lines[0])
        self.assertIn("content", lines[0])
        self.assertNotIn("stub", lines[0])
        self.assertIn("feature-orchestrators/backend/feature.md", lines[1])

    def test_architecture_prompt_lines_marks_stub_documents_distinctly(self):
        component = {
            "id": "content",
            "architecture": {
                "profile_ids": ["ORCH-BE-FEAT"],
                "documents": [
                    {"id": "ORCH-BE-FEAT", "path": "feature-orchestrators/backend/feature.md", "layer": "feature-orchestrators"},
                ],
                "stubs": ["ORCH-BE-FEAT"],
            },
        }
        lines = architecture_prompt_lines(component)
        self.assertEqual(1, len(lines))
        self.assertIn("stub", lines[0])
        self.assertIn("unreviewed", lines[0])


if __name__ == "__main__":
    unittest.main()
