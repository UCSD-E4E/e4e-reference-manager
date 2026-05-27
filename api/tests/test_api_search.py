"""Phase 3a: full-text search over title + abstract + authors (Postgres tsvector).

The list endpoint's ?q= is FTS, not a title substring match: it stems and lowercases,
searches the abstract and author names too, weights title matches above body matches,
and stays scoped to the library the caller can see.
"""


async def _add(client, library, **csl):
    csl.setdefault("type", "document")
    r = await client.post(f"/libraries/{library}/items", json={"csl_json": csl})
    assert r.status_code == 201
    return r.json()


async def test_fts_finds_terms_in_abstract(client, library):
    await _add(
        client,
        library,
        title="Untitled Study",
        abstract="We survey photosynthesis in coral reef ecosystems.",
    )
    await _add(client, library, title="Machine learning")

    r = await client.get(f"/libraries/{library}/items", params={"q": "photosynthesis"})
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["title"] == "Untitled Study"


async def test_fts_finds_author_names(client, library):
    await _add(client, library, title="GANs", author=[{"family": "Goodfellow", "given": "Ian"}])
    await _add(client, library, title="Something else")

    r = await client.get(f"/libraries/{library}/items", params={"q": "goodfellow"})
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["title"] == "GANs"


async def test_fts_stems_and_is_case_insensitive(client, library):
    await _add(client, library, title="Coral Reefs")
    # "coral" (lowercase, singular-ish) should match "Coral Reefs"
    r = await client.get(f"/libraries/{library}/items", params={"q": "coral"})
    assert r.json()["total"] == 1


async def test_fts_ranks_title_match_above_body_match(client, library):
    await _add(client, library, title="Survey of methods", abstract="mentions coral once")
    await _add(client, library, title="Coral reefs")

    r = await client.get(f"/libraries/{library}/items", params={"q": "coral"})
    items = r.json()["items"]
    assert r.json()["total"] == 2
    assert items[0]["title"] == "Coral reefs"  # title (weight A) outranks body (weight B)


async def test_fts_no_match_returns_empty(client, library):
    await _add(client, library, title="Coral reefs")
    r = await client.get(f"/libraries/{library}/items", params={"q": "quantum"})
    assert r.json()["total"] == 0
    assert r.json()["items"] == []


async def test_fts_scoped_to_library(client, library, alice, make_user, login):
    """A term in another user's library must not leak into this library's results."""
    await _add(client, library, title="Coral reefs")

    bob = await make_user("bob@e4e.local", "Bob")
    login(bob)
    other = (await client.post("/libraries", json={"name": "Bob Lib"})).json()["id"]
    await _add(client, other, title="Coral genetics")

    login(alice)
    r = await client.get(f"/libraries/{library}/items", params={"q": "coral"})
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["title"] == "Coral reefs"
