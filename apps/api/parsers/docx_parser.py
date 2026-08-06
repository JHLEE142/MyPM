from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from .archive_safety import MAX_DOCUMENT_BLOCKS


def _iter_blocks(document: Document):
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, document)
        elif child.tag.endswith("}tbl"):
            yield Table(child, document)


def parse_docx(path: str | Path) -> list[dict]:
    document = Document(str(path))
    blocks: list[dict] = []
    section: str | None = None
    for item in _iter_blocks(document):
        if len(blocks) >= MAX_DOCUMENT_BLOCKS:
            raise ValueError("문서 블록 수 초과")
        if isinstance(item, Paragraph):
            content = item.text.strip()
            if not content:
                continue
            style = (item.style.name if item.style else "") or ""
            block_type = "heading" if style.lower().startswith("heading") else "paragraph"
            if block_type == "heading":
                section = content
            elif style.lower().startswith("list"):
                block_type = "list"
            metadata = {"paragraph_index": len(blocks), "style": style}
        else:
            rows = [[cell.text.strip() for cell in row.cells] for row in item.rows]
            content = "\n".join(" | ".join(row) for row in rows)
            if not content.strip():
                continue
            block_type = "table"
            metadata = {"rows": rows, "row_count": len(rows), "column_count": max((len(row) for row in rows), default=0)}
        blocks.append(
            {
                "block_type": block_type,
                "content": content,
                "block_order": len(blocks),
                "page_number": None,
                "sheet_name": None,
                "section_title": section,
                "location_metadata": metadata,
            }
        )
    if not blocks:
        raise ValueError("DOCX에서 읽을 수 있는 텍스트를 찾지 못했습니다")
    return blocks
