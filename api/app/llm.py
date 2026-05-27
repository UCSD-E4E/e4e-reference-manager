"""Local LLM features via Ollama (Phase 3d): auto-tagging + summaries.

`chat` is the single HTTP entry point and is best-effort: any failure returns None so
endpoints degrade gracefully (empty tags / empty summary) instead of 500ing. The pure
prompt-builder and tolerant response parser are unit-tested without a model.
"""
from __future__ import annotations

import json
import logging
import re

import httpx

from .config import get_settings

logger = logging.getLogger("refman")

TAG_SYSTEM = (
    "You are a research librarian. Given a paper's metadata, return ONLY a JSON array of "
    "3 to 7 short, lowercase topical subject tags. No prose, no explanation."
)
SUMMARY_SYSTEM = (
    "You are a research assistant. Summarize the reference for a researcher in 2-3 "
    "concise sentences. Return only the summary."
)
_MAX_BODY_CHARS = 8000


async def chat(prompt: str, *, system: str | None = None) -> str | None:
    """Send a single-turn chat to Ollama. Returns the assistant text, or None on error."""
    s = get_settings()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{s.ollama_url.rstrip('/')}/api/chat",
                json={"model": s.llm_model, "messages": messages, "stream": False},
            )
        r.raise_for_status()
        return (r.json().get("message") or {}).get("content")
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("LLM chat failed (Ollama unavailable?): %s", exc)
        return None


def build_tag_prompt(csl: dict, taxonomy: list[str] | None = None) -> str:
    lines = [
        f"Title: {csl.get('title', '')}",
        f"Abstract: {csl.get('abstract', '')}",
    ]
    if taxonomy:
        lines.append("Prefer tags from this list where applicable: " + ", ".join(taxonomy))
    lines.append("Return a JSON array of tags.")
    return "\n".join(lines)


def _clean_tags(values) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        if not isinstance(v, str):
            continue
        t = v.strip().lower()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out[:10]


def parse_tag_response(text: str | None) -> list[str]:
    """Tolerantly pull a tag list out of an LLM response (array, object, or code-fenced)."""
    if not text:
        return []
    array = re.search(r"\[.*\]", text, re.DOTALL)
    if array:
        try:
            data = json.loads(array.group(0))
            if isinstance(data, list):
                return _clean_tags(data)
        except json.JSONDecodeError:
            pass
    obj = re.search(r"\{.*\}", text, re.DOTALL)
    if obj:
        try:
            data = json.loads(obj.group(0))
            if isinstance(data, dict) and isinstance(data.get("tags"), list):
                return _clean_tags(data["tags"])
        except json.JSONDecodeError:
            pass
    return []


async def suggest_tags(csl: dict, taxonomy: list[str] | None = None) -> list[str]:
    out = await chat(build_tag_prompt(csl, taxonomy), system=TAG_SYSTEM)
    return parse_tag_response(out)


async def summarize(csl: dict, body: str = "") -> str | None:
    prompt = "\n".join(
        [
            f"Title: {csl.get('title', '')}",
            f"Abstract: {csl.get('abstract', '')}",
            (body or "")[:_MAX_BODY_CHARS],
        ]
    )
    return await chat(prompt, system=SUMMARY_SYSTEM)
