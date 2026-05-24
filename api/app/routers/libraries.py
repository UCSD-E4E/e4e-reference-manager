from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..db import get_session
from ..deps import get_owned_library
from ..models import Library, User
from ..schemas import LibraryCreate, LibraryOut

router = APIRouter(prefix="/libraries", tags=["libraries"])


@router.get("", response_model=list[LibraryOut])
async def list_libraries(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
):
    rows = await session.execute(
        select(Library).where(Library.owner_id == user.id).order_by(Library.created_at)
    )
    return list(rows.scalars().all())


@router.post("", response_model=LibraryOut, status_code=201)
async def create_library(
    payload: LibraryCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    lib = Library(name=payload.name, description=payload.description, owner_id=user.id)
    session.add(lib)
    await session.commit()
    await session.refresh(lib)
    return lib


@router.get("/{library_id}", response_model=LibraryOut)
async def get_library(lib: Library = Depends(get_owned_library)):
    return lib
