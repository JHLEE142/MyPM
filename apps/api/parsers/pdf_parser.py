from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader


def parse_pdf(path: str | Path) -> list[dict]:
    reader = PdfReader(str(path))
    blocks: list[dict] = []
    order = 0
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
        if not paragraphs:
            paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
        for paragraph_index, content in enumerate(paragraphs):
            blocks.append(
                {
                    "block_type": "paragraph",
                    "content": content,
                    "block_order": order,
                    "page_number": page_number,
                    "sheet_name": None,
                    "section_title": None,
                    "location_metadata": {"page": page_number, "paragraph_index": paragraph_index},
                }
            )
            order += 1
    if not blocks:
        raise ValueError("텍스트를 추출할 수 없는 PDF입니다. 스캔 PDF OCR은 아직 지원하지 않습니다")
    return blocks
