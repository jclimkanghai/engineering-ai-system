from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .models import EvidenceChunk

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_PAGE_MARKER = re.compile(r"^\s*\[PAGE:\s*(\d+)\]\s*$", re.IGNORECASE)


def _stable_id(document_id: str, locator: str, text: str) -> str:
    raw = f"{document_id}|{locator}|{text}".encode()
    return "ev_" + hashlib.sha256(raw).hexdigest()[:16]


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def chunk_markdown(
    *,
    document_id: str,
    markdown: str,
    document_number: str | None = None,
    title: str | None = None,
    revision: str | None = None,
    revision_date: str | None = None,
    issue_status: str | None = None,
    governing_status: str | None = None,
    discipline: str | None = None,
    target_chars: int = 1800,
    overlap_chars: int = 250,
    metadata: dict[str, Any] | None = None,
) -> list[EvidenceChunk]:
    """Create stable, provenance-preserving evidence chunks from extracted Markdown.

    This is deliberately conservative: it never invents page numbers or source
    locations. A locator is a structural paragraph/section locator unless the
    source text itself provides a page marker.
    """
    lines = markdown.splitlines()
    section = None
    page: int | None = None
    blocks: list[tuple[str | None, int | None, str]] = []
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        text = _normalise(" ".join(buffer))
        if text:
            blocks.append((section, page, text))
        buffer = []

    for line in lines:
        page_match = _PAGE_MARKER.match(line)
        if page_match:
            flush()
            page = int(page_match.group(1))
            continue
        match = _HEADING.match(line)
        if match:
            flush()
            section = match.group(2).strip()
            continue
        if not line.strip():
            flush()
            continue
        buffer.append(line)
    flush()

    chunks: list[EvidenceChunk] = []
    ordinal = 0
    for block_section, block_page, block_text in blocks:
        start = 0
        while start < len(block_text):
            end = min(len(block_text), start + target_chars)
            if end < len(block_text):
                boundary = max(
                    block_text.rfind(". ", start, end),
                    block_text.rfind("; ", start, end),
                    block_text.rfind(" ", start, end),
                )
                if boundary > start + int(target_chars * 0.55):
                    end = boundary + 1
            piece = block_text[start:end].strip()
            if piece:
                ordinal += 1
                page_part = f"page={block_page};" if block_page is not None else ""
                locator = f"{page_part}section={block_section or '<unheaded>'};chunk={ordinal}"
                chunks.append(
                    EvidenceChunk(
                        evidence_id=_stable_id(document_id, locator, piece),
                        document_id=document_id,
                        document_number=document_number,
                        title=title,
                        revision=revision,
                        revision_date=revision_date,
                        issue_status=issue_status,
                        governing_status=governing_status,
                        discipline=discipline,
                        section=block_section,
                        locator=locator,
                        text=piece,
                        metadata={**(metadata or {}), "chunk_ordinal": ordinal},
                        page=block_page,
                    )
                )
            if end >= len(block_text):
                break
            start = max(end - overlap_chars, start + 1)
    return chunks


def chunk_file(path: str | Path, **kwargs: Any) -> list[EvidenceChunk]:
    p = Path(path)
    return chunk_markdown(markdown=p.read_text(encoding="utf-8"), **kwargs)
