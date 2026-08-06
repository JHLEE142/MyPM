from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from .archive_safety import MAX_DOCUMENT_BLOCKS, validate_zip_archive


def parse_hwpx(path: str | Path) -> list[dict]:
    validate_zip_archive(path)
    blocks: list[dict] = []
    with zipfile.ZipFile(path) as archive:
        section_names = sorted(
            name for name in archive.namelist() if re.match(r"Contents/section\d+\.xml$", name, re.IGNORECASE)
        )
        if not section_names:
            raise ValueError("유효한 HWPX 섹션을 찾지 못했습니다. HWPX 또는 PDF로 변환해 다시 업로드해 주세요")
        for section_index, name in enumerate(section_names):
            root = ElementTree.fromstring(archive.read(name))
            paragraph_index = 0
            for element in root.iter():
                if element.tag.rsplit("}", 1)[-1].lower() != "p":
                    continue
                texts = [
                    node.text or "" for node in element.iter() if node.tag.rsplit("}", 1)[-1].lower() == "t"
                ]
                content = "".join(texts).strip()
                if not content:
                    continue
                if len(blocks) >= MAX_DOCUMENT_BLOCKS:
                    raise ValueError("문서 블록 수 초과")
                blocks.append(
                    {
                        "block_type": "paragraph",
                        "content": content,
                        "block_order": len(blocks),
                        "page_number": None,
                        "sheet_name": None,
                        "section_title": f"section{section_index}",
                        "location_metadata": {
                            "xml_path": name,
                            "section_index": section_index,
                            "paragraph_index": paragraph_index,
                        },
                    }
                )
                paragraph_index += 1
    if not blocks:
        raise ValueError("HWPX에서 읽을 수 있는 텍스트를 찾지 못했습니다")
    return blocks
