from __future__ import annotations

from pathlib import Path

from .archive_safety import enforce_block_limits
from .docx_parser import parse_docx
from .hwpx_parser import parse_hwpx
from .pdf_parser import parse_pdf
from .text_parser import parse_text, parse_text_file
from .xlsx_parser import parse_xlsx


class UnsupportedFileTypeError(ValueError):
    pass


def parse_document(path: str | Path, file_type: str | None = None) -> list[dict]:
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
    if suffix not in parsers:
        if suffix == "hwp":
            raise UnsupportedFileTypeError("HWPX 또는 PDF로 변환해 다시 업로드해 주세요")
        raise UnsupportedFileTypeError(f"지원하지 않는 파일 형식입니다: {suffix}")
    return enforce_block_limits(parsers[suffix](path))


__all__ = ["parse_document", "parse_text", "UnsupportedFileTypeError"]
