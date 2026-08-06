from __future__ import annotations

import re
from pathlib import Path


def parse_text(text: str, *, markdown: bool = True) -> list[dict]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if not paragraphs and text.strip():
        paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    blocks: list[dict] = []
    section: str | None = None
    normalized: list[str] = []
    for paragraph in paragraphs:
        lines = paragraph.splitlines()
        if markdown and len(lines) > 1 and re.match(r"^#{1,6}\s+", lines[0].strip()):
            normalized.append(lines[0].strip())
            remainder = "\n".join(lines[1:]).strip()
            if remainder:
                normalized.append(remainder)
        else:
            normalized.append(paragraph)
    for order, paragraph in enumerate(normalized):
        lines = paragraph.splitlines()
        first = lines[0].strip()
        is_heading = bool(re.match(r"^#{1,6}\s+", first))
        if is_heading:
            section = re.sub(r"^#{1,6}\s+", "", first).strip()
        block_type = "heading" if is_heading and len(lines) == 1 else "paragraph"
        if all(re.match(r"^\s*(?:[-*•]|\d+[.)])\s+", line) for line in lines if line.strip()):
            block_type = "list"
        blocks.append(
            {
                "block_type": block_type,
                "content": paragraph,
                "block_order": order,
                "section_title": section,
                "page_number": None,
                "sheet_name": None,
                "location_metadata": {"paragraph_index": order},
            }
        )
    return blocks


def parse_text_file(path: str | Path) -> list[dict]:
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return parse_text(raw.decode(encoding), markdown=Path(path).suffix.lower() == ".md")
        except UnicodeDecodeError:
            continue
    raise ValueError("텍스트 인코딩을 읽을 수 없습니다. UTF-8로 저장해 다시 업로드해 주세요")
