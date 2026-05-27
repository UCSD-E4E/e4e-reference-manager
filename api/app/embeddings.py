"""Local embeddings via Ollama for semantic search (Phase 3c).

`embed_text` is best-effort: any failure (Ollama down, model missing, timeout) is logged
and returns None so writes/search never break — callers treat None as "no embedding".
"""
from __future__ import annotations

import logging

import httpx

from .config import get_settings

logger = logging.getLogger("refman")


def build_embedding_input(csl: dict) -> str:
    """The text we embed for an item: title + abstract (the highest-signal fields)."""
    parts: list[str] = []
    if csl.get("title"):
        parts.append(str(csl["title"]))
    if csl.get("abstract"):
        parts.append(str(csl["abstract"]))
    return "\n".join(parts)


async def embed_text(text: str) -> list[float] | None:
    """Embed a string with the configured Ollama model. Returns None on empty input or
    any error (graceful degradation)."""
    if not text or not text.strip():
        return None
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{s.ollama_url.rstrip('/')}/api/embeddings",
                json={"model": s.embedding_model, "prompt": text},
            )
        r.raise_for_status()
        vec = r.json().get("embedding")
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Embedding failed (Ollama unavailable?): %s", exc)
        return None
    if not vec:
        return None
    return vec


async def embed_item_csl(csl: dict) -> list[float] | None:
    """Convenience: build the input text for an item's CSL and embed it."""
    return await embed_text(build_embedding_input(csl))
