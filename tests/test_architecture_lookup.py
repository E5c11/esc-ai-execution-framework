import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from esc_exec.architecture_lookup import (
    load_architecture_index,
    resolve_architecture_docs,
    stub_documents,
)


def _doc(doc_id: str, layer: str, requires: list[str] | None = None, status: str = "") -> dict:
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


if __name__ == "__main__":
    unittest.main()
