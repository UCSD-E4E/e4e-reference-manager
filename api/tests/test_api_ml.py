"""Phase 3d: LLM auto-tagging + summaries via Ollama.

Ollama is never hit: tests monkeypatch app.llm.chat (the single HTTP entry point) with
canned responses. A None from chat (model down) must degrade gracefully — no 500s.
"""
import app.llm as llm
from app.llm import build_tag_prompt, parse_tag_response


async def _add(client, library, **csl):
    csl.setdefault("type", "document")
    r = await client.post(f"/libraries/{library}/items", json={"csl_json": csl})
    assert r.status_code == 201
    return r.json()


# --- pure helpers ---

def test_build_tag_prompt_includes_title_and_abstract():
    p = build_tag_prompt({"title": "Coral genomics", "abstract": "We sequence corals."})
    assert "Coral genomics" in p
    assert "sequence corals" in p


def test_parse_tag_response_plain_array():
    assert parse_tag_response('["Machine Learning", "Coral"]') == ["machine learning", "coral"]


def test_parse_tag_response_code_fence_and_prose():
    raw = 'Sure! Here are the tags:\n```json\n["Reefs", "Ecology"]\n```'
    assert parse_tag_response(raw) == ["reefs", "ecology"]


def test_parse_tag_response_object_form():
    assert parse_tag_response('{"tags": ["a", "B", "a"]}') == ["a", "b"]  # dedup + lower


def test_parse_tag_response_garbage_returns_empty():
    assert parse_tag_response("I cannot help with that.") == []
    assert parse_tag_response("") == []


# --- endpoints ---

async def test_suggest_tags_returns_without_persisting(client, library, monkeypatch):
    async def fake_chat(prompt, **kw):
        return '["coral reefs", "machine learning"]'

    monkeypatch.setattr(llm, "chat", fake_chat)
    item = await _add(client, library, title="A paper", abstract="about reefs and ML")

    r = await client.post(f"/items/{item['id']}/suggest-tags")
    assert r.status_code == 200
    assert r.json()["suggestions"] == ["coral reefs", "machine learning"]
    assert r.json()["applied"] == []
    # nothing persisted
    assert (await client.get(f"/items/{item['id']}/tags")).json() == []


async def test_suggest_tags_apply_persists_ml_tags_and_dedups(client, library, monkeypatch):
    async def fake_chat(prompt, **kw):
        return '["coral reefs", "machine learning"]'

    monkeypatch.setattr(llm, "chat", fake_chat)
    item = await _add(client, library, title="A paper", abstract="reefs")

    r = await client.post(f"/items/{item['id']}/suggest-tags", params={"apply": "true"})
    assert r.status_code == 200
    applied = r.json()["applied"]
    assert {t["name"] for t in applied} == {"coral reefs", "machine learning"}
    assert all(t["source"] == "ml" for t in applied)

    tags = (await client.get(f"/items/{item['id']}/tags")).json()
    assert {t["name"] for t in tags} == {"coral reefs", "machine learning"}

    # Applying again must not duplicate.
    await client.post(f"/items/{item['id']}/suggest-tags", params={"apply": "true"})
    tags2 = (await client.get(f"/items/{item['id']}/tags")).json()
    assert len(tags2) == 2


async def test_suggest_tags_graceful_when_llm_down(client, library, monkeypatch):
    async def dead_chat(prompt, **kw):
        return None

    monkeypatch.setattr(llm, "chat", dead_chat)
    item = await _add(client, library, title="A paper")

    r = await client.post(f"/items/{item['id']}/suggest-tags", params={"apply": "true"})
    assert r.status_code == 200
    assert r.json()["suggestions"] == []
    assert r.json()["applied"] == []


async def test_summary_endpoint(client, library, monkeypatch):
    async def fake_chat(prompt, **kw):
        return "This paper studies coral reef bleaching."

    monkeypatch.setattr(llm, "chat", fake_chat)
    item = await _add(client, library, title="Reefs", abstract="bleaching study")

    r = await client.post(f"/items/{item['id']}/summary")
    assert r.status_code == 200
    assert "coral reef" in r.json()["summary"]


async def test_summary_graceful_when_llm_down(client, library, monkeypatch):
    async def dead_chat(prompt, **kw):
        return None

    monkeypatch.setattr(llm, "chat", dead_chat)
    item = await _add(client, library, title="Reefs")

    r = await client.post(f"/items/{item['id']}/summary")
    assert r.status_code == 200
    assert r.json()["summary"] == ""
