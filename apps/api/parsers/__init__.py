from __future__ import annotations

from pathlib import Path

from .archive_safety import enforce_block_limits
from .docx_parser import parse_docx
from .generic_parser import IMAGE_EXTENSIONS, IMAGE_MIME, parse_generic, parse_image
from .hwpx_parser import parse_hwpx
from .pdf_parser import parse_pdf
from .text_parser import parse_text, parse_text_file
from .xlsx_parser import parse_xlsx


class UnsupportedFileTypeError(ValueError):
    pass


def parse_document(path: str | Path, file_type: str | None = None, original_name: str = "") -> list[dict]:
    path = Path(path)
    suffix = (file_type or path.suffix).lower().lstrip(".")
    parsers = {
        "pdf": parse_pdf,
        "docx": parse_docx,
        "xlsx": parse_xlsx,
        "txt": parse_text_file,
        "md": parse_text_file,
        "hwpx": parse_hwpx,
    }
    if suffix in parsers:
        return enforce_block_limits(parsers[suffix](path))
    if suffix in IMAGE_EXTENSIONS:
        return enforce_block_limits(parse_image(path, original_name))
    # 그 외 모든 형식: 텍스트 추출 시도 후 실패하면 메타데이터 블록으로 수용
    return enforce_block_limits(parse_generic(path, original_name))


__all__ = [
    "parse_document",
    "parse_text",
    "UnsupportedFileTypeError",
    "IMAGE_EXTENSIONS",
    "IMAGE_MIME",
]
