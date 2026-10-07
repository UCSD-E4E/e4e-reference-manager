"""Paste BibTeX: preview (parse + duplicate check + validation, nothing saved), then add
the chosen entries. Registrar lookups are monkeypatched (see test_api_validation)."""
import pytest

import app.validation as v

REAL = "@article{real,title={Attention Is All You Need},year={2017},doi={10.1/real}}"
FAKE = "@article{fake,title={A Paper That Does Not Exist},year={2023},doi={10.1/fake}}"
NODOI = "@misc{nodoi,title={Some Unindexed Report},year={2019}}"
PASTE = "\n".join([REAL, FAKE, NODOI])


@pytest.fixture
def registrars(monkeypatch):
    calls = []

    async def lookup(doi):
        calls.append(doi)
        return {"title": "Attention Is All You Need"} if doi == "10.1/real" else None

    async def search(title, authors=None):
        return None

    monkeypatch.setattr(v, "crossref_lookup", lookup)
    monkeypatch.setattr(v, "crossref_search", search)
    v.clear_validation_cache()
    return calls


async def _preview(client, library, text=PASTE):
    r = await client.post(f"/libraries/{library}/import/preview", json={"bibtex": text})
    assert r.status_code == 200
    return r.json()["entries"]


async def _count(client, library):
    return (await client.get(f"/libraries/{library}/items")).json()["total"]


async def test_preview_parses_and_validates_without_saving(client, library, registrars):
    entries = await _preview(client, library)

    assert [(e["index"], e["citation_key"]) for e in entries] == [
        (0, "real"),
        (1, "fake"),
        (2, "nodoi"),
    ]
    assert [e["validation"]["status"] for e in entries] == [
        "verified",
        "not_found",
        "unverifiable",
    ]
    assert entries[0]["title"] == "Attention Is All You Need" and entries[0]["year"] == 2017
    assert all(e["duplicate"] is None for e in entries)
    assert await _count(client, library) == 0


async def test_preview_flags_duplicates_and_skips_validating_them(client, library, registrars):
    await client.post(f"/libraries/{library}/import/text", json={"bibtex": REAL})
    registrars.clear()

    entries = await _preview(client, library, REAL.replace("{real,", "{again,") + "\n" + FAKE)
    assert entries[0]["duplicate"]["matched_on"] == "doi"
    assert entries[0]["validation"] is None
    assert registrars == ["10.1/fake"]  # the duplicate wasn't looked up


async def test_preview_flags_repeats_within_the_paste(client, library, registrars):
    entries = await _preview(client, library, REAL + "\n" + REAL.replace("{real,", "{real2,"))
    assert entries[0]["duplicate"] is None
    assert entries[1]["duplicate"] == {"item_id": None, "entry_index": 0, "matched_on": "doi"}


async def test_add_selected_entries_stores_their_verdicts(client, library, registrars):
    await _preview(client, library)
    registrars.clear()

    r = await client.post(
        f"/libraries/{library}/import/text", json={"bibtex": PASTE, "indices": [0, 2]}
    )
    assert r.status_code == 200
    assert r.json()["imported"] == 2
    assert registrars == []  # verdicts reused from the preview, not looked up again

    items = (await client.get(f"/libraries/{library}/items")).json()["items"]
    by_key = {i["citation_key"]: i for i in items}
    assert set(by_key) == {"real", "nodoi"}
    assert by_key["real"]["validation"]["status"] == "verified"
    assert by_key["nodoi"]["validation"]["status"] == "unverifiable"


async def test_add_without_preview_validates_and_dedupes(client, library, registrars):
    r = await client.post(f"/libraries/{library}/import/text", json={"bibtex": REAL + "\n" + REAL})
    assert r.json()["imported"] == 1
    assert len(r.json()["duplicates"]) == 1
    item = (await client.get(f"/libraries/{library}/items")).json()["items"][0]
    assert item["validation"]["status"] == "verified"


async def test_garbage_paste_is_a_clear_error(client, library, registrars):
    r = await client.post(f"/libraries/{library}/import/preview", json={"bibtex": "hello"})
    assert r.status_code == 400
    assert "No BibTeX entries" in r.json()["detail"]


async def test_view_only_access_cannot_preview_or_add(client, make_user, login, registrars):
    alice = await make_user("alice@x")
    bob = await make_user("bob@x")
    login(bob)
    group = (await client.post("/groups", json={"slug": "team", "name": "Team"})).json()
    login(alice)
    lib = (await client.post("/libraries", json={"name": "A"})).json()["id"]
    await client.post(
        f"/libraries/{lib}/shares", json={"group_id": group["id"], "access_level": "view"}
    )
    login(bob)
    assert (
        await client.post(f"/libraries/{lib}/import/preview", json={"bibtex": REAL})
    ).status_code == 403
    assert (
        await client.post(f"/libraries/{lib}/import/text", json={"bibtex": REAL})
    ).status_code == 403
