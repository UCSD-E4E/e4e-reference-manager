"""Access control: ownership, group ownership, shares, and org-admin."""


async def _make_item(client, lib):
    return (
        await client.post(
            f"/libraries/{lib}/items", json={"csl_json": {"type": "document", "title": "x"}}
        )
    ).json()


async def test_other_users_private_library_is_invisible(client, make_user, login):
    alice = await make_user("alice@x")
    bob = await make_user("bob@x")

    login(bob)
    lib = (await client.post("/libraries", json={"name": "Bob"})).json()["id"]

    login(alice)
    assert (await client.get(f"/libraries/{lib}")).status_code == 404
    assert (await client.get(f"/libraries/{lib}/items")).status_code == 404
    assert (
        await client.post(f"/libraries/{lib}/items", json={"csl_json": {"type": "document"}})
    ).status_code == 404
    assert all(item["id"] != lib for item in (await client.get("/libraries")).json())


async def test_group_owned_library_visible_to_members_only(client, make_user, login):
    alice = await make_user("alice@x")
    bob = await make_user("bob@x")

    login(alice)
    group = (await client.post("/groups", json={"slug": "lab", "name": "Lab"})).json()
    lib = (
        await client.post("/libraries", json={"name": "Shared", "owner_group_id": group["id"]})
    ).json()["id"]

    login(bob)
    assert (await client.get(f"/libraries/{lib}")).status_code == 404  # not a member yet

    login(alice)
    await client.post(f"/groups/{group['id']}/members", json={"email": "bob@x"})

    login(bob)
    got = await client.get(f"/libraries/{lib}")
    assert got.status_code == 200
    assert got.json()["my_access"] == "manage"  # group members manage


async def test_view_share_allows_read_but_not_write(client, make_user, login):
    alice = await make_user("alice@x")
    bob = await make_user("bob@x")

    login(bob)
    group = (await client.post("/groups", json={"slug": "team", "name": "Team"})).json()

    login(alice)
    lib = (await client.post("/libraries", json={"name": "Alice lib"})).json()["id"]
    share = await client.post(
        f"/libraries/{lib}/shares", json={"group_id": group["id"], "access_level": "view"}
    )
    assert share.status_code == 200

    login(bob)
    assert (await client.get(f"/libraries/{lib}/items")).status_code == 200  # view ok
    denied = await client.post(
        f"/libraries/{lib}/items", json={"csl_json": {"type": "document"}}
    )
    assert denied.status_code == 403  # needs edit


async def test_admin_sees_everything(client, make_user, login):
    bob = await make_user("bob@x")
    admin = await make_user("admin@x", org_role="admin")

    login(bob)
    lib = (await client.post("/libraries", json={"name": "Bob"})).json()["id"]

    login(admin)
    assert (await client.get(f"/libraries/{lib}")).status_code == 200
    assert any(item["id"] == lib for item in (await client.get("/libraries")).json())


async def test_non_manager_cannot_manage_shares(client, make_user, login):
    alice = await make_user("alice@x")
    bob = await make_user("bob@x")

    login(bob)
    group = (await client.post("/groups", json={"slug": "g", "name": "G"})).json()

    login(alice)
    lib = (await client.post("/libraries", json={"name": "Alice"})).json()["id"]
    await client.post(
        f"/libraries/{lib}/shares", json={"group_id": group["id"], "access_level": "edit"}
    )

    login(bob)  # has edit via share, not manage
    assert (await client.get(f"/libraries/{lib}/shares")).status_code == 403
