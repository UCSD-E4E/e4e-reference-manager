"""Topic auto-groups: cluster a project's papers by embedding, let the model name each
cluster, and give every paper one topic tag so each topic is a live tag auto-group.

Ollama is never hit: embed_text and chat are monkeypatched (see test_api_semantic)."""
import pytest

import app.embeddings as emb
import app.llm as llm
from app.models import EMBEDDING_DIM
from app.topics import choose_k, cluster, parse_topic_name

# ---------- pure helpers ----------


def test_choose_k_scales_with_project_size():
    assert choose_k(6) == 2
    assert choose_k(50) == 5
    assert choose_k(200) == 10
    assert choose_k(5000) == 15  # capped


def test_cluster_separates_obvious_groups_deterministically():
    a = [[1.0, 0.0, 0.01 * i] for i in range(5)]
    b = [[0.0, 1.0, 0.01 * i] for i in range(5)]
    labels = cluster(a + b, 2)
    assert len(set(labels[:5])) == 1 and len(set(labels[5:])) == 1
    assert labels[0] != labels[5]
    assert cluster(a + b, 2) == labels


@pytest.mark.parametrize(
    "raw,name",
    [
        ("Coral Reef Ecology", "coral reef ecology"),
        ('"Genomics."\n\nThese papers all...', "genomics"),
        ("Topic: machine learning", "machine learning"),
        ("", None),
    ],
)
def test_parse_topic_name(raw, name):
    assert parse_topic_name(raw) == name


# ---------- endpoint ----------


def _vec(axis: int, jitter: float) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[axis] = 1.0
    v[2] = jitter
    return v


async def fake_embed(text):
    i = len(text) % 7 * 0.01
    return _vec(0, i) if "reef" in text.lower() else _vec(1, i)


async def fake_name(prompt, **kw):
    return "coral reefs" if "Reef" in prompt else "genomics"


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setattr(emb, "embed_text", fake_embed)
    monkeypatch.setattr(llm, "chat", fake_name)


async def _add(client, library, title):
    r = await client.post(
        f"/libraries/{library}/items", json={"csl_json": {"type": "article", "title": title}}
    )
    assert r.status_code == 201


async def _seed(client, library, n=6):
    for i in range(n):
        await _add(client, library, f"Reef survey {i}")
        await _add(client, library, f"Genome assembly {i}")


async def _topic_groups(client, library):
    groups = (await client.get(f"/libraries/{library}/auto-groups")).json()
    out = {}
    for g in groups:
        if g["kind"] == "tag" and g["params"].get("source") == "topic":
            items = (await client.get(f"/auto-groups/{g['id']}/items")).json()
            out[g["name"]] = sorted(i["title"] for i in items)
    return out


async def _generate(client, library):
    return await client.post(f"/libraries/{library}/auto-groups/generate", json={"source": "topics"})


async def test_topics_cover_every_paper_with_named_groups(client, library, model):
    await _seed(client, library)

    r = await _generate(client, library)
    assert r.status_code == 200
    assert r.json() == {"created": 2, "clustered": 12, "unclustered": 0}

    groups = await _topic_groups(client, library)
    assert set(groups) == {"coral reefs", "genomics"}
    assert all("Reef" in t for t in groups["coral reefs"]) and len(groups["coral reefs"]) == 6
    assert all("Genome" in t for t in groups["genomics"]) and len(groups["genomics"]) == 6


async def test_regenerating_replaces_previous_topics(client, library, model):
    await _seed(client, library)
    await _generate(client, library)
    await _add(client, library, "Reef survey extra")
    await _generate(client, library)

    groups = await _topic_groups(client, library)
    assert set(groups) == {"coral reefs", "genomics"}  # replaced, not duplicated
    assert len(groups["coral reefs"]) == 7


async def test_papers_without_embeddings_get_one_computed(client, library, monkeypatch):
    monkeypatch.setattr(emb, "embed_text", lambda text: _none())  # Ollama down at import
    await _seed(client, library)
    monkeypatch.setattr(emb, "embed_text", fake_embed)  # back up when grouping
    monkeypatch.setattr(llm, "chat", fake_name)

    r = await _generate(client, library)
    assert r.json()["clustered"] == 12


async def _none():
    return None


async def test_too_few_papers_is_a_clear_error(client, library, model):
    for i in range(3):
        await _add(client, library, f"Reef survey {i}")
    r = await _generate(client, library)
    assert r.status_code == 400
    assert "at least" in r.json()["detail"]
    assert await _topic_groups(client, library) == {}


async def test_model_down_keeps_existing_topics(client, library, model, monkeypatch):
    await _seed(client, library)
    await _generate(client, library)

    async def down(prompt, **kw):
        return None

    monkeypatch.setattr(llm, "chat", down)
    r = await _generate(client, library)
    assert r.status_code == 503
    assert set(await _topic_groups(client, library)) == {"coral reefs", "genomics"}


async def test_clusters_with_the_same_name_merge(client, library, monkeypatch):
    monkeypatch.setattr(emb, "embed_text", fake_embed)

    async def same(prompt, **kw):
        return "marine science"

    monkeypatch.setattr(llm, "chat", same)
    await _seed(client, library)
    r = await _generate(client, library)
    assert r.json()["created"] == 1
    groups = await _topic_groups(client, library)
    assert list(groups) == ["marine science"] and len(groups["marine science"]) == 12
