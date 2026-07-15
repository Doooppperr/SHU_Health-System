from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass


CHUNK_NAMESPACE = uuid.UUID("10c3f719-f3cb-44ee-a1e7-bf9b02f6dbcc")


@dataclass(frozen=True)
class ExtractedSection:
    locator: str
    text: str


@dataclass(frozen=True)
class KnowledgeChunk:
    point_id: str
    chunk_id: str
    locator: str
    content: str


def normalize_text(value: str) -> str:
    value = value.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def chunk_sections(
    source_id: str,
    version: str,
    sections: list[ExtractedSection],
    *,
    target_chars: int = 320,
    hard_max_chars: int = 480,
    overlap_chars: int = 60,
) -> list[KnowledgeChunk]:
    chunks = []
    for section in sections:
        text = normalize_text(section.text)
        if not text:
            continue
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n", text) if item.strip()]
        buffer = ""
        raw_chunks = []
        for paragraph in paragraphs:
            while len(paragraph) > hard_max_chars:
                if buffer:
                    raw_chunks.append(buffer)
                    buffer = ""
                cut = paragraph.rfind("。", 0, hard_max_chars)
                if cut < target_chars:
                    cut = hard_max_chars
                else:
                    cut += 1
                raw_chunks.append(paragraph[:cut])
                paragraph = paragraph[max(0, cut - overlap_chars) :]
            candidate = f"{buffer}\n\n{paragraph}".strip() if buffer else paragraph
            if len(candidate) <= hard_max_chars:
                buffer = candidate
                if len(buffer) >= target_chars:
                    raw_chunks.append(buffer)
                    buffer = buffer[-overlap_chars:]
            else:
                if buffer:
                    raw_chunks.append(buffer)
                prefix = buffer[-overlap_chars:] if buffer else ""
                buffer = f"{prefix}{paragraph}"[:hard_max_chars]
        if buffer and (not raw_chunks or buffer != raw_chunks[-1]):
            raw_chunks.append(buffer)

        for index, content in enumerate(raw_chunks, start=1):
            content = normalize_text(content)
            if not content:
                continue
            digest = hashlib.sha256(
                f"{source_id}|{version}|{section.locator}|{index}|{content}".encode("utf-8")
            ).hexdigest()
            chunks.append(
                KnowledgeChunk(
                    point_id=str(uuid.uuid5(CHUNK_NAMESPACE, digest)),
                    chunk_id=digest,
                    locator=f"{section.locator} · 片段 {index}",
                    content=content,
                )
            )
    return chunks
