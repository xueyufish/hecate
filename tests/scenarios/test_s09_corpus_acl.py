"""S09 — synthetic corpus ACL visibility and citation anchor resolution.

Manifest: S09 (P01, P06). The corpus proves that scenarios can pin knowledge
snapshots with versions, permissions, and canonical citation positions:

- ACL filtering happens before ranking — unauthorized documents are invisible,
  not merely down-ranked (P06 slice),
- citations resolve to the anchors declared in corpus.yaml (P01 slice),
- multiple versions of the same procedure stay separately addressable.
"""

from __future__ import annotations

import yaml

from tests.scenarios.tools.corpus_index import CORPUS_DIR, CorpusIndex


def test_s09_corpus_acl_retrieval_visibility() -> None:
    index = CorpusIndex()

    auditor_hits = index.search("claim approved finding", principal="auditor")
    auditor_docs = {hit.doc_id for hit in auditor_hits}
    assert "audit-notes-2026h1" in auditor_docs, "auditor must see the restricted notes"

    employee_hits = index.search("claim approved finding", principal="employee")
    assert all(hit.doc_id != "audit-notes-2026h1" for hit in employee_hits), (
        "restricted audit notes must be invisible to non-auditor principals"
    )


def test_s09_citation_anchors_resolve_to_declared_positions() -> None:
    index = CorpusIndex()
    manifest = yaml.safe_load((CORPUS_DIR / "corpus.yaml").read_text(encoding="utf-8"))

    declared = {c["anchor"] for doc in manifest["documents"] for c in doc.get("citations", [])}
    assert declared <= index.anchors, "every declared citation anchor must exist in the documents"

    hits = index.search("reimbursement limit", principal="employee")
    assert hits, "authorized principal must retrieve the procedure"
    top = hits[0]
    assert top.doc_id == "proc-reimburse"
    assert top.version == "2.1"
    assert top.anchor == "sec-limit-standard"
    assert "800 credits" in top.text


def test_s09_versioned_documents_stay_separately_addressable() -> None:
    index = CorpusIndex()

    legacy_hits = index.search("legacy limit 500", principal="employee")
    assert legacy_hits, "the superseded version must remain retrievable"
    legacy = [hit for hit in legacy_hits if hit.doc_id == "proc-reimburse-v1"]
    assert legacy and all(hit.version == "1.0" for hit in legacy), (
        "the superseded version must stay addressable under its own version"
    )

    current_hits = index.search("standard per-claim limit", principal="finance_agent")
    assert current_hits and current_hits[0].version == "2.1", (
        "current version must be retrievable independently of the legacy one"
    )
