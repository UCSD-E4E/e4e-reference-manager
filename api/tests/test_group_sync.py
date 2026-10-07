"""sync_user_groups: reconciling Authentik `groups` claims into memberships."""
from sqlalchemy import func, select

from app.auth import sync_user_groups
from app.models import Group, user_group


async def _memberships(db, user):
    return await db.scalar(
        select(func.count()).select_from(user_group).where(user_group.c.user_id == user.id)
    )


async def test_duplicate_group_names_in_claim_create_one_membership(db, make_user):
    # Authentik's lab `groups` mapping can list a group more than once (seen in prod:
    # the callback 500'd on user_group_pkey).
    user = await make_user("bob@e4e.local")
    await sync_user_groups(db, user, ["E4E Admins", "E4E Admins", "Kastner Research Group"])

    assert await _memberships(db, user) == 2
    assert await db.scalar(select(func.count()).select_from(Group)) == 2


async def test_relogin_with_same_groups_is_idempotent(db, make_user):
    user = await make_user("bob@e4e.local")
    await sync_user_groups(db, user, ["E4E Admins"])
    await sync_user_groups(db, user, ["E4E Admins", "E4E Admins"])

    assert await _memberships(db, user) == 1
