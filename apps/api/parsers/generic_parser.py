from __future__ import annotations

from pathlib import Path

from .text_parser import parse_text


IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff", "svg"}
IMAGE_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "tiff": "image/tiff",
    "svg": "image/svg+xml",
}
MAX_TEXT_DECODE_BYTES = 2 * 1024 * 1024


def parse_image(path: str | Path, original_name: str = "") -> list[dict]:
    """이미지 파일은 메타데이터 블록 하나로 시작한다. AI 설명은 라우트 단계에서 덧붙인다."""
    path = Path(path)
    size_kb = max(1, path.stat().st_size // 1024)
    label = original_name or path.name
    return [
        {
            "block_type": "image",
            "content": f"[이미지 파일] {label} ({size_kb}KB)",
            "block_order": 0,
            "page_number": None,
            "sheet_name": None,
            "section_title": None,
            "location_metadata": {"kind": "uploaded_image", "file_name": label},
        }
    ]


def _looks_like_text(raw: bytes) -> str | None:
    if b"\x00" in raw[:4096]:
        return None
    for encoding in ("utf-8", "cp949"):
        try:
            decoded = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        sample = decoded[:2000]
        if not sample.strip():
            return None
        printable = sum(1 for ch in sample if ch.isprintable() or ch in "\n\r\t")
        if printable / max(1, len(sample)) >= 0.9:
            return decoded
    return None


def parse_generic(path: str | Path, original_name: str = "") -> list[dict]:
    """미지원 형식: 텍스트로 읽히면 텍스트 블록, 아니면 메타데이터 블록."""
    path = Path(path)
    label = original_name or path.name
    size = path.stat().st_size
    if size <= MAX_TEXT_DECODE_BYTES:
        decoded = _looks_like_text(path.read_bytes())
        if decoded is not None:
            return parse_text(decoded)
    size_kb = max(1, size // 1024)
    return [
        {
            "block_type": "file",
            "content": f"[파일] {label} ({size_kb}KB) — 본문 텍스트를 추출하지 못했습니다. 파일 존재만 참고합니다.",
            "block_order": 0,
            "page_number": None,
            "sheet_name": None,
            "section_title": None,
            "location_metadata": {"kind": "binary_file", "file_name": label},
        }
    ]
