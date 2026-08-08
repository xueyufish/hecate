# Tutorial: Knowledge Base and RAG

> Documentation in progress. This tutorial will cover creating a knowledge base, uploading documents, configuring chunking and embedding, and querying through an agent.

## What you will build

An agent that answers questions from your own documents using retrieval-augmented generation (RAG).

## Prerequisites

- Hecate running locally (see [Quickstart](../getting-started/quickstart.md))
- At least one document to upload (PDF, Markdown, or plain text)

## Steps (outline)

1. Create a knowledge base via `POST /api/knowledge-bases`
2. Upload documents via `POST /api/knowledge-bases/{id}/documents`
3. Wait for parsing and embedding to complete
4. Attach the knowledge base to an agent
5. Chat with the agent — it will retrieve relevant chunks before answering

## Further reading

- [RAG Pipeline Design](../design/rag-pipeline-design.md)
- [Knowledge & Memory Design](../design/knowledge-memory-design.md)