from __future__ import annotations

import ipaddress
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx


MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 3
MAX_BLOCKS = 500
MAX_IMAGE_BLOCKS = 30
FETCH_TIMEOUT = 15.0
USER_AGENT = "MyPM-SourceFetcher/1.0"


class UrlFetchError(ValueError):
    """사용자에게 그대로 보여줄 수 있는 URL 수집 실패 사유."""


def _validate_public_http_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise UrlFetchError("http/https URL만 지원합니다")
    if not parsed.hostname:
        raise UrlFetchError("올바른 URL이 아닙니다")
    if parsed.username or parsed.password:
        raise UrlFetchError("자격증명이 포함된 URL은 지원하지 않습니다")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except OSError as exc:
        raise UrlFetchError("URL의 호스트를 찾을 수 없습니다") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise UrlFetchError("내부망·사설 IP로 연결되는 URL은 가져올 수 없습니다")
    return parsed.geturl()


def _fetch_html(url: str) -> tuple[str, str]:
    """리다이렉트 홉마다 재검증하며 HTML을 가져온다. (final_url, html) 반환."""
    current = _validate_public_http_url(url)
    with httpx.Client(follow_redirects=False, timeout=FETCH_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            with client.stream("GET", current) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise UrlFetchError("리다이렉트 대상이 없습니다")
                    current = _validate_public_http_url(urljoin(current, location))
                    continue
                if response.status_code != 200:
                    raise UrlFetchError(f"페이지를 가져오지 못했습니다 (HTTP {response.status_code})")
                content_type = response.headers.get("content-type", "")
                if "html" not in content_type and not content_type.startswith("text/"):
                    raise UrlFetchError("HTML/텍스트 페이지만 지원합니다")
                collected = bytearray()
                for chunk in response.iter_bytes():
                    collected.extend(chunk)
                    if len(collected) > MAX_HTML_BYTES:
                        raise UrlFetchError("페이지가 너무 큽니다 (5MB 초과)")
                encoding = response.charset_encoding or "utf-8"
                try:
                    return current, collected.decode(encoding, errors="replace")
                except LookupError:
                    return current, collected.decode("utf-8", errors="replace")
    raise UrlFetchError("리다이렉트가 너무 많습니다")


_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "iframe"}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_BREAK_TAGS = {"p", "li", "div", "section", "article", "br", "tr", "table", "blockquote", "pre"}


class _PageExtractor(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.title = ""
        self._in_title = False
        self._skip_depth = 0
        self._section = ""
        self._heading_tag: str | None = None
        self._buffer: list[str] = []
        self.items: list[dict] = []  # {kind: heading|text|image, content, section}

    def _flush_text(self) -> None:
        text = " ".join("".join(self._buffer).split())
        self._buffer = []
        if len(text) >= 2:
            self.items.append({"kind": "text", "content": text[:4000], "section": self._section})

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = True
        elif tag in _HEADING_TAGS:
            self._flush_text()
            self._heading_tag = tag
        elif tag in _BREAK_TAGS:
            self._flush_text()
        elif tag == "img":
            attributes = dict(attrs)
            src = (attributes.get("src") or "").strip()
            if src and not src.startswith("data:"):
                alt = " ".join((attributes.get("alt") or "").split())
                self.items.append({
                    "kind": "image",
                    "content": f"[이미지] {alt or '설명 없음'} — {urljoin(self.base_url, src)}"[:2000],
                    "section": self._section,
                })

    def handle_endtag(self, tag: str):
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = False
        elif tag in _HEADING_TAGS and self._heading_tag == tag:
            heading = " ".join("".join(self._buffer).split())
            self._buffer = []
            self._heading_tag = None
            if heading:
                self._section = heading[:500]
                self.items.append({"kind": "heading", "content": heading[:1000], "section": self._section})
        elif tag in _BREAK_TAGS:
            self._flush_text()

    def handle_data(self, data: str):
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
        else:
            self._buffer.append(data)


def fetch_url_blocks(url: str) -> tuple[str, list[dict]]:
    """URL을 가져와 (페이지 제목, source_block 목록)을 반환한다."""
    final_url, html = _fetch_html(url)
    extractor = _PageExtractor(final_url)
    extractor.feed(html)
    extractor._flush_text()

    blocks: list[dict] = []
    image_count = 0
    for item in extractor.items:
        if len(blocks) >= MAX_BLOCKS:
            break
        if item["kind"] == "image":
            if image_count >= MAX_IMAGE_BLOCKS:
                continue
            image_count += 1
        blocks.append(
            {
                "block_type": item["kind"] if item["kind"] != "text" else "paragraph",
                "content": item["content"],
                "block_order": len(blocks),
                "page_number": None,
                "sheet_name": None,
                "section_title": item["section"] or None,
                "location_metadata": {"source_url": final_url},
            }
        )
    if not blocks:
        raise UrlFetchError("페이지에서 추출할 텍스트를 찾지 못했습니다")
    title = " ".join(extractor.title.split())[:255]
    return title or final_url[:255], blocks
