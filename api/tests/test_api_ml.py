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


# --- apply exactly what was shown; generate tags untagged items first ---

async def test_apply_saves_the_given_tags_without_asking_the_model_again(
    client, library, monkeypatch
):
    async def boom(prompt, **kw):
        raise AssertionError("the model must not be called when tags are supplied")

    monkeypatch.setattr(llm, "chat", boom)
    item = await _add(client, library, title="A paper")
    r = await client.post(
        f"/items/{item['id']}/suggest-tags",
        params={"apply": True},
        json={"tags": ["Coral Reefs", "coral reefs", "ML"]},
    )
    assert r.status_code == 200
    names = sorted(t["name"] for t in (await client.get(f"/items/{item['id']}/tags")).json())
    assert names == ["coral reefs", "ml"]
    assert all(t["source"] == "ml" for t in r.json()["applied"])


async def test_generate_from_ml_tags_tags_untagged_items_first(client, library, monkeypatch):
    calls = []

    async def fake_chat(prompt, **kw):
        calls.append(prompt)
        # "reefs" is shared by three papers; the other tag is unique to each
        title = prompt.split("Title: ")[1].splitlines()[0].lower()
        return f'["reefs", "{title}"]'

    monkeypatch.setattr(llm, "chat", fake_chat)
    for title in ("Reef survey", "Reef census", "Reef bleaching"):
        await _add(client, library, title=title)

    r = await client.post(f"/libraries/{library}/auto-groups/generate", json={"source": "ml_tags"})
    assert r.status_code == 200
    # one group: only tags shared by at least 3 papers become groups
    assert r.json() == {"created": 1, "tagged": 3, "remaining": 0}
    names = [g["name"] for g in (await client.get(f"/libraries/{library}/auto-groups")).json()]
    assert names == ["reefs"]

    # already-tagged items aren't sent to the model again
    calls.clear()
    again = (
        await client.post(f"/libraries/{library}/auto-groups/generate", json={"source": "ml_tags"})
    ).json()
    assert calls == []
    assert again == {"created": 0, "tagged": 0, "remaining": 0}


async def test_generate_from_ml_tags_caps_model_calls_per_request(client, library, monkeypatch):
    import app.routers.auto_groups as ag

    async def fake_chat(prompt, **kw):
        return '["t"]'

    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(ag, "ML_TAG_BATCH", 2)
    for i in range(3):
        await _add(client, library, title=f"Paper {i}")

    body = (
        await client.post(f"/libraries/{library}/auto-groups/generate", json={"source": "ml_tags"})
    ).json()
    assert body["tagged"] == 2
    assert body["remaining"] == 1


async def test_generate_from_ml_tags_with_model_down_reports_untagged(
    client, library, monkeypatch
):
    async def down(prompt, **kw):
        return None

    monkeypatch.setattr(llm, "chat", down)
    await _add(client, library, title="Paper")
    body = (
        await client.post(f"/libraries/{library}/auto-groups/generate", json={"source": "ml_tags"})
    ).json()
    assert body == {"created": 0, "tagged": 0, "remaining": 1}


async def test_generate_from_ml_tags_stops_at_first_empty_answer(client, library, monkeypatch):
    calls = []

    async def down(prompt, **kw):
        calls.append(prompt)
        return None

    monkeypatch.setattr(llm, "chat", down)
    for i in range(3):
        await _add(client, library, title=f"Paper {i}")
    body = (
        await client.post(f"/libraries/{library}/auto-groups/generate", json={"source": "ml_tags"})
    ).json()
    assert len(calls) == 1  # a down model costs one timeout, not one per item
    assert body == {"created": 0, "tagged": 0, "remaining": 3}
