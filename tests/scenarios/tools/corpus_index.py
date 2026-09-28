"""ACL-aware corpus index for the scenario pack's synthetic documents.

Loads ``corpus/corpus.yaml`` plus its markdown documents and exposes a
deterministic, hermetic retrieval surface used by the corpus ACL scenario
(S09) and the Tier-2 baseline runner. This is a test fixture, not a product
component: it exists so corpus versioning, ACL tagging, and citation anchors
can be verified independently of any real vector store (real-store replacement
tests belong to step9d).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CORPUS_DIR = Path(__file__).resolve().parent.parent / "corpus"


@dataclass
class CorpusChunk:
    """One retrievable unit with its citation anchor and ACL visibility."""

    doc_id: str
    version: str
    anchor: str
    text: str
    acl_read: frozenset[str]


@dataclass
class CorpusHit:
    """A retrieval hit with its resolved citation anchor."""

    doc_id: str
    version: str
    anchor: str
    text: str
    score: float


class CorpusIndex:
    """Deterministic keyword index over the synthetic corpus with ACL filtering."""

    def __init__(self, corpus_dir: Path = CORPUS_DIR) -> None:
        self._corpus_dir = corpus_dir
        self._docs: dict[str, dict[str, Any]] = {}
        self._chunks: list[CorpusChunk] = []
        self._load()

    def _load(self) -> None:
        manifest = yaml.safe_load((self._corpus_dir / "corpus.yaml").read_text(encoding="utf-8"))
        for doc in manifest["documents"]:
            doc_id = doc["id"]
            self._docs[doc_id] = doc
            text = (self._corpus_dir / doc["file"]).read_text(encoding="utf-8")
            self._chunks.extend(self._split(doc, text))

    @staticmethod
    def _split(doc: dict[str, Any], text: str) -> list[CorpusChunk]:
        acl_read = frozenset(doc.get("acl", {}).get("read", []))
        chunks: list[CorpusChunk] = []
        current_anchor: str | None = None
        current_lines: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("<!-- anchor:"):
                if current_anchor is not None:
                    chunks.append(
                        CorpusChunk(doc["id"], doc["version"], current_anchor, "\n".join(current_lines), acl_read)
                    )
                current_anchor = stripped.removeprefix("<!-- anchor:").removesuffix("-->").strip()
                current_lines = []
            elif current_anchor is not None:
                current_lines.append(line)
        if current_anchor is not None:
            chunks.append(CorpusChunk(doc["id"], doc["version"], current_anchor, "\n".join(current_lines), acl_read))
        return chunks

    def doc(self, doc_id: str) -> dict[str, Any]:
        return self._docs[doc_id]

    @property
    def anchors(self) -> set[str]:
        """All citation anchors declared by the corpus manifest and documents."""
        return {chunk.anchor for chunk in self._chunks}

    def search(self, query: str, principal: str) -> list[CorpusHit]:
        """Keyword search restricted to documents the principal may read.

        ACL filtering happens before ranking: a chunk from a document whose
        ``acl.read`` list excludes the principal is never returned, so
        unauthorized content is invisible rather than merely down-ranked.
        """
        terms = [t for t in query.lower().split() if t]
        hits: list[CorpusHit] = []
        for chunk in self._chunks:
            if principal not in chunk.acl_read:
                continue
            lowered = chunk.text.lower()
            matched = sum(1 for t in terms if t in lowered)
            if matched:
                hits.append(
                    CorpusHit(
                        doc_id=chunk.doc_id,
                        version=chunk.version,
                        anchor=chunk.anchor,
                        text=chunk.text,
                        score=matched / len(terms),
                    )
                )
        return sorted(hits, key=lambda h: (-h.score, h.doc_id, h.anchor))
