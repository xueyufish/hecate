# ADR-017: Knowledge Graph Architecture with GraphStore ABC

> **Status**: Proposed
> **Date**: 2026-07-01

## Context

Hecate's current knowledge system is RAG-centric: documents are chunked, embedded, and stored in vector databases (Qdrant, Chroma). This works for text retrieval but cannot answer questions that require understanding entity relationships, multi-hop connections, or structured domain knowledge.

Competitive analysis (Palantir AIP, Huawei AgentArts, AgentScope, MemGPT/Letta) shows that enterprise-grade agent platforms need a Knowledge Graph as the backbone for:
- Entity-centric retrieval (find all entities related to X)
- Multi-hop reasoning (A → B → C traversal)
- Structured knowledge representation (typed entities + typed relationships)
- Ontology grounding for OAG (ADR-015)

The question is how to integrate a Knowledge Graph without coupling the engine to a specific graph database.

## Decision

Implement a **GraphStore ABC** (abstract base class) in the engine layer with two concrete backends, plus an LLM-powered entity/relationship extraction pipeline:

### 1. GraphStore ABC

```python
class GraphStore(ABC):
    @abstractmethod
    async def add_entities(self, entities: list[Entity]) -> None: ...

    @abstractmethod
    async def add_relations(self, relations: list[Relation]) -> None: ...

    @abstractmethod
    async def query(self, cypher: str, params: dict) -> list[dict]: ...

    @abstractmethod
    async def search_entities(self, query: str, limit: int) -> list[Entity]: ...

    @abstractmethod
    async def get_neighbors(self, entity_id: str, depth: int) -> SubGraph: ...

    @abstractmethod
    async def detect_communities(self, algorithm: str) -> list[Community]: ...
```

### 2. Concrete Backends

- **Neo4jGraphStore** — Production backend using Neo4j 5.x with Cypher query language. Supports full-text search, graph algorithms (PageRank, Louvain community detection), and ACID transactions.
- **InMemoryGraphStore** — Default backend for development and testing. Uses NetworkX-style adjacency lists. Supports basic traversal and greedy modularity community detection.

### 3. Extraction Pipeline

```
Document → Chunk → LLM Entity Extraction → LLM Relation Extraction
                                                      │
                                          Entity Resolution (dedup)
                                                      │
                                          GraphStore.add_entities()
                                          GraphStore.add_relations()
```

The extraction pipeline runs asynchronously (via Temporal workflow or background task) and uses configurable LLM prompts to identify entities (people, organizations, concepts, etc.) and their relationships (works_for, located_in, depends_on, etc.).

### 4. Community Detection

Graph clustering (Louvain, Leiden algorithms) groups related entities into communities, enabling:
- **GraphRAG** — Retrieve at community level for broader context
- **Entity summarization** — Generate summaries per community
- **Knowledge exploration** — Visualize domain structure in Agent Studio

## Rationale

- **ABC over direct integration**: The engine must remain database-agnostic. Neo4j is the recommended production backend, but the ABC allows future support for Amazon Neptune, TigerGraph, or ArangoDB without engine changes.

- **InMemory default**: Enables zero-config development and unit testing without a running Neo4j instance, consistent with the pattern established by InMemoryCheckpointStore and InMemoryEventStore.

- **LLM extraction over rule-based**: LLM-based entity/relation extraction handles heterogeneous document formats and domain-specific terminology better than NER-only approaches. The extraction prompts are configurable per knowledge base.

- **Community detection**: Inspired by Microsoft GraphRAG research (2024), community-level retrieval provides broader context than chunk-level retrieval, improving answer quality for "big picture" questions.

- **Builds on existing infrastructure**: The extraction pipeline extends the existing document processing pipeline (chunking, embedding). The GraphStore is consumed via `EnginePort.knowledge_query`, maintaining the engine's dependency rules.

## Consequences

- Feature 3.5.1 (Knowledge Graph Construction) must implement the extraction pipeline
- Feature 3.5.2 (Graph Database Integration) provides the Neo4jGraphStore adapter
- Feature 3.5.3 (Community Detection) implements graph clustering algorithms
- The GraphStore ABC is a new Core extension point alongside CheckpointStore and EventStore
- Neo4j becomes an optional dependency in the `[rag]` dependency group (not required for core operation)
- ADR-014 (Ontology Action System) and ADR-015 (OAG) both depend on this Knowledge Graph infrastructure
