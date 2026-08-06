from __future__ import annotations

import io
import struct
import zlib

import pytest

from parsers.url_fetcher import UrlFetchError, _validate_public_http_url


def _tiny_png() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(
            ">I", zlib.crc32(kind + payload) & 0xFFFFFFFF
        )

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = zlib.compress(b"\x00\xff\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file",
        "http://localhost/admin",
        "http://127.0.0.1:8000/",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5/internal",
        "http://user:pass@example.com/",
    ],
)
def test_url_validation_rejects_unsafe_targets(url):
    with pytest.raises(UrlFetchError):
        _validate_public_http_url(url)


def test_url_source_creates_blocks_without_network(client, project_payload, monkeypatch):
    project = client.post("/api/projects", json=project_payload).json()

    def fake_fetch(url):
        assert url == "https://example.com/spec"
        return "예시 사양 문서", [
            {
                "block_type": "heading",
                "content": "프로젝트 목표",
                "block_order": 0,
                "page_number": None,
                "sheet_name": None,
                "section_title": "프로젝트 목표",
                "location_metadata": {"source_url": url},
            },
            {
                "block_type": "image",
                "content": "[이미지] 일정 간트 스크린샷 — https://example.com/plan.png",
                "block_order": 1,
                "page_number": None,
                "sheet_name": None,
                "section_title": "프로젝트 목표",
                "location_metadata": {"source_url": url},
            },
        ]

    monkeypatch.setattr("app.api.routes.fetch_url_blocks", fake_fetch)
    response = client.post(
        f"/api/projects/{project['id']}/sources", json={"url": "https://example.com/spec"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["file_type"] == "url"
    assert body["file_name"] == "예시 사양 문서"
    assert body["analysis_status"] == "text_extracted"
    assert [block["block_type"] for block in body["blocks"]] == ["heading", "image"]


def test_image_upload_creates_image_block_with_optional_caption(client, project_payload, monkeypatch):
    project = client.post("/api/projects", json=project_payload).json()
    monkeypatch.setattr("app.api.routes.describe_image_safely", lambda raw, mime: "간트 차트 일정 이미지")
    response = client.post(
        f"/api/projects/{project['id']}/sources",
        files={"file": ("plan.png", io.BytesIO(_tiny_png()), "image/png")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["analysis_status"] == "text_extracted"
    assert body["blocks"][0]["block_type"] == "image"
    assert "이미지 내용: 간트 차트 일정 이미지" in body["blocks"][0]["content"]


def test_any_file_upload_is_accepted(client, project_payload, monkeypatch):
    project = client.post("/api/projects", json=project_payload).json()
    monkeypatch.setattr("app.api.routes.describe_image_safely", lambda raw, mime: None)
    text_like = client.post(
        f"/api/projects/{project['id']}/sources",
        files={"file": ("notes.json", io.BytesIO(b'{"todo": "release"}'), "application/json")},
    )
    assert text_like.status_code == 201, text_like.text
    assert text_like.json()["analysis_status"] == "text_extracted"

    binary = client.post(
        f"/api/projects/{project['id']}/sources",
        files={"file": ("archive.xyz", io.BytesIO(b"\x00\x01\x02binary"), "application/octet-stream")},
    )
    assert binary.status_code == 201, binary.text
    blocks = binary.json()["blocks"]
    assert blocks[0]["block_type"] == "file"
    assert "추출하지 못했습니다" in blocks[0]["content"]


def test_analysis_auto_approve_when_gate_disabled(client, project_payload, monkeypatch):
    monkeypatch.delenv("AI_REVIEW_GATE", raising=False)
    project = client.post("/api/projects", json=project_payload).json()
    source = client.post(
        f"/api/projects/{project['id']}/sources",
        json={"file_name": "회의.md", "text": "# 목표\n출시 준비\n\n## 업무\n- 배포 스크립트 작성 2시간"},
    )
    assert source.status_code == 201
    assert client.post(f"/api/projects/{project['id']}/analysis").status_code == 202
    status = client.get(f"/api/projects/{project['id']}/analysis/status").json()
    assert status["status"] == "completed"
    tasks = client.get(f"/api/projects/{project['id']}/tasks").json()
    ai_tasks = [task for task in tasks if task["ai_generated"]]
    # 자동 승인 + 자동 일정 배치까지 이어지므로 리프는 scheduled, 상위는 approved가 된다.
    assert ai_tasks and all(task["status"] in {"approved", "scheduled"} for task in ai_tasks)
    sources = client.get(f"/api/projects/{project['id']}/sources").json()
    assert all(item["analysis_status"] == "completed" for item in sources)


def test_auto_approve_analysis_also_generates_schedule(client, project_payload, monkeypatch):
    monkeypatch.delenv("AI_REVIEW_GATE", raising=False)
    project = client.post("/api/projects", json=project_payload).json()
    client.post(
        f"/api/projects/{project['id']}/sources",
        json={"file_name": "회의.md", "text": "# 업무\n- 기획 정리 2시간\n- 개발 착수 3시간"},
    )
    assert client.post(f"/api/projects/{project['id']}/analysis").status_code == 202
    schedule = client.get(f"/api/projects/{project['id']}/schedule").json()
    assert schedule["version"] == 1
    assert schedule["reason"] == "AI 분석 자동 배치"
    placements = schedule["schedule_snapshot"]["placements"]
    assert placements
    leaf_ids = {
        task["id"]
        for task in client.get(f"/api/projects/{project['id']}/tasks").json()
        if task["cadence"] in ("daily", None)
    }
    assert {item["task_id"] for item in placements} <= leaf_ids
