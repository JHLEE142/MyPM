from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any


MAX_ARCHIVE_UNCOMPRESSED_SIZE = 50 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_BLOCK_CONTENT_BYTES = 64 * 1024
MAX_DOCUMENT_BLOCKS = 5000


def validate_zip_archive(path: str | Path) -> None:
    with zipfile.ZipFile(path) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        total_size = sum(info.file_size for info in infos)
        total_compressed = sum(info.compress_size for info in infos)
        if total_size > MAX_ARCHIVE_UNCOMPRESSED_SIZE:
            raise ValueError("압축 해제 크기 초과")
        if total_size and (total_compressed == 0 or total_size / total_compressed > MAX_COMPRESSION_RATIO):
            raise ValueError("압축비 초과")


def enforce_block_limits(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(blocks) > MAX_DOCUMENT_BLOCKS:
        raise ValueError("문서 블록 수 초과")
    for block in blocks:
        content = str(block.get("content", ""))
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_BLOCK_CONTENT_BYTES:
            block["content"] = encoded[:MAX_BLOCK_CONTENT_BYTES].decode("utf-8", errors="ignore")
    return blocks
