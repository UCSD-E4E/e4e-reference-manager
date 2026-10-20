"""Size caps for uploaded files, enforced while reading so a huge upload is never held
in memory whole. (Starlette has already spooled the multipart body to a temp file by
then; a reverse-proxy body limit is what stops the bytes arriving at all.)"""
from __future__ import annotations

from fastapi import HTTPException, UploadFile, status

MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_BIB_BYTES = 20 * 1024 * 1024
_CHUNK = 1024 * 1024


async def _read_capped(file: UploadFile, limit: int, what: str) -> bytes:
    data = bytearray()
    while chunk := await file.read(_CHUNK):
        data += chunk
        if len(data) > limit:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"{what} are limited to {limit / (1024 * 1024):g} MB",
            )
    return bytes(data)


async def read_pdf_upload(file: UploadFile) -> bytes:
    """The uploaded file's bytes, or 413 past MAX_PDF_BYTES."""
    return await _read_capped(file, MAX_PDF_BYTES, "PDFs")


async def read_bib_upload(file: UploadFile) -> bytes:
    """The uploaded .bib file's bytes, or 413 past MAX_BIB_BYTES."""
    return await _read_capped(file, MAX_BIB_BYTES, ".bib files")
