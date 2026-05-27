"""PDF plain-text extraction (PyMuPDF) for full-text search and, later, embeddings.

Synchronous/CPU-bound; callers wrap in run_in_threadpool. Extraction never raises on a
bad/non-PDF input — it returns "" so an upload is never blocked by an unreadable file.
"""
from __future__ import annotations

import logging

import fitz  # PyMuPDF

logger = logging.getLogger("refman")

# Cap text fed into a single item's tsvector: Postgres limits a tsvector to ~1 MiB of
# lexemes, and this is plenty of body text for recall.
MAX_PDF_TEXT_CHARS = 500_000


def extract_pdf_text(data: bytes) -> str:
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            text = "\n".join(page.get_text() for page in doc)
    except Exception as exc:  # corrupt / not a PDF — don't block the upload
        logger.warning("PDF text extraction failed: %s", exc)
        return ""
    return text.strip()
