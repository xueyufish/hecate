"""Knowledge base management commands.

Provides:
- hecate kb list
- hecate kb create
- hecate kb upload <kb_id> <file>
- hecate kb documents <kb_id>
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from hecate.cli.client import HecateClient
from hecate.cli.config import get_output_format, get_profile_name
from hecate.cli.output import display_result

app = typer.Typer(no_args_is_help=True)


@app.command()
def list(
    page: Annotated[int, typer.Option("--page", help="Page number")] = 1,
    page_size: Annotated[int, typer.Option("--page-size", help="Items per page")] = 20,
) -> None:
    """List all knowledge bases."""
    client = HecateClient(get_profile_name())
    result = client.get("/api/knowledge-bases", params={"page": page, "page_size": page_size})
    display_result(
        result,
        get_output_format(),
        columns=["id", "name", "description", "search_mode"],
        title="Knowledge Bases",
    )


@app.command()
def create(
    name: Annotated[str, typer.Option("--name", "-n", help="Knowledge base name")],
    description: Annotated[str | None, typer.Option("--description", "-d", help="Description")] = None,
    embedding_model: Annotated[str, typer.Option("--embedding-model", help="Embedding model")] = "BAAI/bge-m3",
    chunk_strategy: Annotated[str, typer.Option("--chunk-strategy", help="Chunking strategy")] = "auto",
) -> None:
    """Create a new knowledge base."""
    client = HecateClient(get_profile_name())

    body: dict = {
        "name": name,
        "embedding_model": embedding_model,
        "chunk_strategy": chunk_strategy,
    }
    if description:
        body["description"] = description

    result = client.post("/api/knowledge-bases", json=body)
    display_result(result, get_output_format(), title="Knowledge Base Created")


@app.command()
def upload(
    kb_id: Annotated[str, typer.Argument(help="Knowledge base UUID")],
    file_path: Annotated[str, typer.Argument(help="Path to file to upload")],
) -> None:
    """Upload a document to a knowledge base."""
    path = Path(file_path)
    if not path.exists():
        typer.echo(f"Error: File not found: {file_path}")
        raise typer.Exit(1)

    client = HecateClient(get_profile_name())

    with open(path, "rb") as f:
        result = client.post(
            f"/api/knowledge-bases/{kb_id}/documents",
            files={"file": (path.name, f, "application/octet-stream")},
            content_type="multipart/form-data",
        )

    display_result(result, get_output_format(), title="Document Uploaded")


@app.command()
def documents(
    kb_id: Annotated[str, typer.Argument(help="Knowledge base UUID")],
    page: Annotated[int, typer.Option("--page", help="Page number")] = 1,
    page_size: Annotated[int, typer.Option("--page-size", help="Items per page")] = 20,
) -> None:
    """List documents in a knowledge base."""
    client = HecateClient(get_profile_name())
    result = client.get(f"/api/knowledge-bases/{kb_id}/documents", params={"page": page, "page_size": page_size})
    display_result(
        result,
        get_output_format(),
        columns=["id", "filename", "parsing_status", "chunk_count", "created_at"],
        title=f"Documents in KB {kb_id}",
    )
