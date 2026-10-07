""".bib import de-duplicates against the project (and within the file): the same paper
is matched by DOI, else by normalized title + year, and skipped instead of re-added."""

A = b"@article{smith2020,title={Deep {L}earning for Reefs},author={Smith, J},year={2020},doi={10.1000/ABC}}\n"


async def _import(client, library, name, data):
    r = await client.post(f"/libraries/{library}/import", files={"file": (name, data)})
    assert r.status_code == 200
    return r.json()


async def _count(client, library):
    return (await client.get(f"/libraries/{library}/items")).json()["total"]


async def test_second_file_with_same_doi_is_skipped(client, library):
    await _import(client, library, "a.bib", A)
    # different key, DOI in another case/with a resolver prefix
    b = b"@article{smith_reefs,title={Deep learning for reefs},year={2020},doi={https://doi.org/10.1000/abc}}\n"
    r = await _import(client, library, "b.bib", b)

    assert r["imported"] == 0
    assert [(d["citation_key"], d["matched_on"]) for d in r["duplicates"]] == [("smith_reefs", "doi")]
    assert await _count(client, library) == 1


async def test_same_title_and_year_without_doi_is_skipped(client, library):
    await _import(client, library, "a.bib", A)
    b = b"@inproceedings{s20,title={DEEP LEARNING FOR REEFS.},year={2020}}\n"
    r = await _import(client, library, "b.bib", b)

    assert r["imported"] == 0
    assert r["duplicates"][0]["matched_on"] == "title"
    assert await _count(client, library) == 1


async def test_same_title_different_year_is_not_a_duplicate(client, library):
    await _import(client, library, "a.bib", A)
    b = b"@article{s21,title={Deep Learning for Reefs},year={2021}}\n"
    r = await _import(client, library, "b.bib", b)

    assert r["imported"] == 1
    assert r["duplicates"] == []


async def test_duplicates_within_one_file_are_skipped(client, library):
    r = await _import(client, library, "a.bib", A + A.replace(b"smith2020", b"smith2020b"))

    assert r["imported"] == 1
    assert len(r["duplicates"]) == 1
    assert await _count(client, library) == 1


async def test_reimporting_the_same_file_adds_nothing(client, library):
    await _import(client, library, "a.bib", A)
    r = await _import(client, library, "a.bib", A)

    assert r["imported"] == 0
    assert r["key_collisions"] == []  # a duplicate, not a key collision
    assert await _count(client, library) == 1


async def test_different_paper_reusing_a_key_is_kept_and_flagged(client, library):
    await _import(client, library, "a.bib", A)
    other = b"@article{smith2020,title={Something else entirely},year={2020}}\n"
    r = await _import(client, library, "b.bib", other)

    assert r["imported"] == 1
    assert r["key_collisions"] == ["smith2020"]
    assert await _count(client, library) == 2
