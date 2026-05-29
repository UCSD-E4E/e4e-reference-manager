"""Phase 7: JabRef-style auto-groups (live, rule-driven).

Three rule kinds:
- field: one group per unique value of year/type/journal/author
- tag:   items carrying a specific tag (optionally filtered by source=ml|manual)
- search: items matching an FTS query

Membership is recomputed on demand, never stored. A generate endpoint bulk-creates one
group per distinct value (year/author/etc.) or per ml-suggested tag.
"""


async def _item(client, lib, *, key, **csl):
    csl.setdefault("type", "article-journal")
    return (
        await client.post(
            f"/libraries/{lib}/items",
            json={"citation_key": key, "csl_json": csl},
        )
    ).json()


# ---------- create + items + export ----------

async def test_create_and_list_by_field_year(client, library):
    await _item(client, library, key="a", title="A", issued={"date-parts": [[2020]]})
    await _item(client, library, key="b", title="B", issued={"date-parts": [[2020]]})
    await _item(client, library, key="c", title="C", issued={"date-parts": [[2021]]})

    r = await client.post(
        f"/libraries/{library}/auto-groups",
        json={"name": "2020", "kind": "field", "params": {"field": "year", "value": "2020"}},
    )
    assert r.status_code == 201
    ag = r.json()
    assert ag["count"] == 2
    assert ag["name"] == "2020"

    listed = await client.get(f"/libraries/{library}/auto-groups")
    assert [g["name"] for g in listed.json()] == ["2020"]

    items = await client.get(f"/auto-groups/{ag['id']}/items")
    assert {it["citation_key"] for it in items.json()} == {"a", "b"}


async def test_by_field_journal_and_type(client, library):
    await _item(
        client,
        library,
        key="j1",
        title="X",
        **{"container-title": "Nature"},
    )
    await _item(
        client,
        library,
        key="j2",
        title="Y",
        **{"container-title": "Nature"},
    )
    await _item(client, library, key="j3", title="Z", **{"container-title": "Science"})
    await _item(client, library, key="b1", type="book", title="Book")

    nature = await client.post(
        f"/libraries/{library}/auto-groups",
        json={
            "name": "Nature",
            "kind": "field",
            "params": {"field": "journal", "value": "Nature"},
        },
    )
    assert nature.json()["count"] == 2

    books = await client.post(
        f"/libraries/{library}/auto-groups",
        json={"name": "Books", "kind": "field", "params": {"field": "type", "value": "book"}},
    )
    assert books.json()["count"] == 1


async def test_by_field_author_matches_any_author(client, library):
    await _item(
        client,
        library,
        key="p1",
        title="P1",
        author=[{"family": "Smith", "given": "A"}, {"family": "Jones", "given": "B"}],
    )
    await _item(client, library, key="p2", title="P2", author=[{"family": "Smith", "given": "C"}])
    await _item(client, library, key="p3", title="P3", author=[{"family": "Doe"}])

    smith = await client.post(
        f"/libraries/{library}/auto-groups",
        json={
            "name": "Smith",
            "kind": "field",
            "params": {"field": "author", "value": "Smith"},
        },
    )
    assert smith.json()["count"] == 2
    items = (await client.get(f"/auto-groups/{smith.json()['id']}/items")).json()
    assert {it["citation_key"] for it in items} == {"p1", "p2"}


async def test_by_search_query(client, library):
    await _item(client, library, key="r1", title="Photosynthesis in coral reefs")
    await _item(client, library, key="r2", title="Quantum mechanics primer")

    r = await client.post(
        f"/libraries/{library}/auto-groups",
        json={
            "name": "Coral",
            "kind": "search",
            "params": {"q": "coral", "mode": "keyword"},
        },
    )
    assert r.json()["count"] == 1
    items = (await client.get(f"/auto-groups/{r.json()['id']}/items")).json()
    assert items[0]["citation_key"] == "r1"


async def test_by_tag_filters_by_name_and_source(client, library, monkeypatch):
    # Seed two items + apply tags via the suggest-tags endpoint (apply=true persists
    # them as ml-sourced Tags, which is what we want to filter on).
    import app.llm as llm

    async def fake_chat(_prompt, **_kw):
        return '["machine learning"]'

    monkeypatch.setattr(llm, "chat", fake_chat)

    a = await _item(client, library, key="t1", title="ML paper")
    await _item(client, library, key="t2", title="Bio paper")  # stays untagged
    await client.post(f"/items/{a['id']}/suggest-tags", params={"apply": "true"})

    r = await client.post(
        f"/libraries/{library}/auto-groups",
        json={
            "name": "ML",
            "kind": "tag",
            "params": {"name": "machine learning", "source": "ml"},
        },
    )
    assert r.json()["count"] == 1
    items = (await client.get(f"/auto-groups/{r.json()['id']}/items")).json()
    assert items[0]["citation_key"] == "t1"


async def test_export_bib_for_auto_group(client, library):
    await _item(client, library, key="exA", title="A", issued={"date-parts": [[2020]]})
    await _item(client, library, key="skipB", title="B", issued={"date-parts": [[2021]]})
    ag = (
        await client.post(
            f"/libraries/{library}/auto-groups",
            json={"name": "2020", "kind": "field", "params": {"field": "year", "value": "2020"}},
        )
    ).json()
    bib = await client.get(f"/auto-groups/{ag['id']}/export.bib")
    assert bib.status_code == 200
    assert "exA" in bib.text
    assert "skipB" not in bib.text


# ---------- generate ----------

async def test_generate_by_year_creates_one_per_distinct_year_and_is_idempotent(
    client, library
):
    await _item(client, library, key="y20", title="A", issued={"date-parts": [[2020]]})
    await _item(client, library, key="y21a", title="B", issued={"date-parts": [[2021]]})
    await _item(client, library, key="y21b", title="C", issued={"date-parts": [[2021]]})
    # Item with no year is skipped.
    await _item(client, library, key="ny", title="D")

    r = await client.post(
        f"/libraries/{library}/auto-groups/generate", json={"source": "year"}
    )
    assert r.status_code == 200
    assert r.json()["created"] == 2

    # Idempotent: a second call creates nothing new.
    r2 = await client.post(
        f"/libraries/{library}/auto-groups/generate", json={"source": "year"}
    )
    assert r2.json()["created"] == 0

    groups = (await client.get(f"/libraries/{library}/auto-groups")).json()
    assert sorted(g["name"] for g in groups) == ["2020", "2021"]


async def test_generate_from_ml_tags(client, library, monkeypatch):
    import app.llm as llm

    async def fake_chat(_prompt, **_kw):
        return '["coral reefs", "machine learning"]'

    monkeypatch.setattr(llm, "chat", fake_chat)
    a = await _item(client, library, key="m1", title="MLp")
    await client.post(f"/items/{a['id']}/suggest-tags", params={"apply": "true"})

    r = await client.post(
        f"/libraries/{library}/auto-groups/generate", json={"source": "ml_tags"}
    )
    assert r.json()["created"] == 2
    groups = (await client.get(f"/libraries/{library}/auto-groups")).json()
    assert sorted(g["name"] for g in groups) == ["coral reefs", "machine learning"]
    assert all(g["kind"] == "tag" for g in groups)


# ---------- delete + rbac ----------

async def test_delete_auto_group(client, library):
    ag = (
        await client.post(
            f"/libraries/{library}/auto-groups",
            json={"name": "X", "kind": "field", "params": {"field": "year", "value": "2020"}},
        )
    ).json()
    assert (await client.delete(f"/auto-groups/{ag['id']}")).status_code == 204
    assert (await client.get(f"/libraries/{library}/auto-groups")).json() == []


async def test_auto_group_rbac(client, make_user, login):
    alice = await make_user("alice@x")
    bob = await make_user("bob@x")
    carol = await make_user("carol@x")

    login(bob)
    group = (await client.post("/groups", json={"slug": "t", "name": "T"})).json()

    login(alice)
    lib = (await client.post("/libraries", json={"name": "A"})).json()["id"]
    ag = (
        await client.post(
            f"/libraries/{lib}/auto-groups",
            json={"name": "X", "kind": "field", "params": {"field": "year", "value": "2020"}},
        )
    ).json()
    await client.post(
        f"/libraries/{lib}/shares", json={"group_id": group["id"], "access_level": "view"}
    )

    login(carol)
    assert (await client.get(f"/libraries/{lib}/auto-groups")).status_code == 404

    login(bob)  # view share
    assert (await client.get(f"/libraries/{lib}/auto-groups")).status_code == 200
    assert (await client.get(f"/auto-groups/{ag['id']}/export.bib")).status_code == 200
    denied = await client.post(
        f"/libraries/{lib}/auto-groups",
        json={"name": "Y", "kind": "field", "params": {"field": "year", "value": "2021"}},
    )
    assert denied.status_code == 403
