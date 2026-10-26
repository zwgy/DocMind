"""验证真实文档从上传、解析、入库到检索的闭环。"""

from __future__ import annotations

import asyncio
import time
import uuid
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


async def _wait_for_document_task(client, headers, task_id: str) -> dict:
    """同时检查后台终态与逐文件结果，避免任务成功掩盖单文件失败。"""
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        response = await client.get(f"/api/tasks/{task_id}", headers=headers)
        assert response.status_code == 200, response.text
        task = response.json()["task"]
        if task["status"] in {"success", "failed", "cancelled"}:
            assert task["status"] == "success", task
            result = task["result"]
            assert result["failed"] == 0, result
            assert len(result["items"]) == 1, result
            assert result["items"][0]["status"] not in {"failed", "error_parsing", "error_indexing"}, result
            return result
        await asyncio.sleep(2)
    pytest.fail(f"文档任务超时：{task_id}")


@pytest.mark.parametrize("source_kind", ["text", "scan", "scan_pdf"])
async def test_document_upload_parse_index_and_retrieve(e2e_client, e2e_headers, source_kind):
    """文本和无文字层的扫描图均须生成可追溯的真实检索结果。"""
    suffix = uuid.uuid4().hex[:10]
    text = "【测试】部署验收专用资料\n青岚设备的校验码是 846213。\n此资料只用于知识库闭环测试。"
    if source_kind in {"scan", "scan_pdf"}:
        # 容器镜像已有中文字体；图片不带文字层，必须经过真实 OCR 才能检索。
        font_path = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
        assert font_path.exists(), "运行此用例的镜像必须包含项目规定的中文字体"
        image = Image.new("RGB", (1500, 400), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(str(font_path), 48)
        draw.multiline_text((60, 50), text, font=font, fill="black", spacing=30)
        buffer = BytesIO()
        if source_kind == "scan_pdf":
            image.save(buffer, format="PDF", resolution=150)
            filename, mime_type = f"scan-{suffix}.pdf", "application/pdf"
        else:
            image.save(buffer, format="PNG")
            filename, mime_type = f"scan-{suffix}.png", "image/png"
        content = buffer.getvalue()
    else:
        filename, content, mime_type = f"text-{suffix}.txt", text.encode(), "text/plain"

    models = await e2e_client.get(
        "/api/system/model-providers/models/v2", params={"model_type": "embedding"}, headers=e2e_headers
    )
    assert models.status_code == 200, models.text
    specs = [
        model["spec"]
        for provider in models.json()["data"].values()
        for model in provider.get("models", [])
        if model.get("spec")
    ]
    assert specs, "知识库端到端验收需要已启用的真实向量模型"
    created = await e2e_client.post(
        "/api/knowledge/databases",
        json={
            "database_name": f"pytest_ingestion_{source_kind}_{suffix}",
            "description": "【测试】部署验收入库与检索",
            "embedding_model_spec": specs[0],
            "kb_type": "milvus",
            "additional_params": {},
        },
        headers=e2e_headers,
    )
    assert created.status_code == 200, created.text
    kb_id = created.json()["kb_id"]
    task_ids = []
    try:
        uploaded = await e2e_client.post(
            "/api/knowledge/files/upload",
            params={"kb_id": kb_id},
            files={"file": (filename, content, mime_type)},
            headers=e2e_headers,
        )
        assert uploaded.status_code == 200, uploaded.text
        upload = uploaded.json()
        path = upload["file_path"]
        added = await e2e_client.post(
            f"/api/knowledge/databases/{kb_id}/documents/add",
            json={
                "items": [path],
                "params": {
                    "content_type": "file",
                    "content_hashes": {path: upload["content_hash"]},
                    "file_sizes": {path: upload["size"]},
                    "ocr_engine": "system_default",
                },
            },
            headers=e2e_headers,
        )
        assert added.status_code == 200, added.text
        assert len(added.json()["items"]) == 1, added.text
        file_id = added.json()["items"][0]["file_id"]

        for action, expected_status in (("parse", "parsed"), ("index", "indexed")):
            submitted = await e2e_client.post(
                f"/api/knowledge/databases/{kb_id}/documents/{action}",
                json=[file_id] if action == "parse" else {"file_ids": [file_id]},
                headers=e2e_headers,
            )
            assert submitted.status_code == 200, submitted.text
            task_id = submitted.json()["task_id"]
            task_ids.append(task_id)
            await _wait_for_document_task(e2e_client, e2e_headers, task_id)
            info = await e2e_client.get(
                f"/api/knowledge/databases/{kb_id}/documents/{file_id}/basic", headers=e2e_headers
            )
            assert info.status_code == 200, info.text
            assert info.json()["meta"]["status"] == expected_status, info.text

        stored = await e2e_client.get(
            f"/api/knowledge/databases/{kb_id}/documents/{file_id}/content", headers=e2e_headers
        )
        assert stored.status_code == 200, stored.text
        assert "846213" in stored.json()["content"], stored.text
        assert stored.json()["lines"], "入库后 PostgreSQL 应保存实际分块"

        for mode in ("vector", "keyword", "hybrid"):
            queried = await e2e_client.post(
                f"/api/knowledge/databases/{kb_id}/query",
                json={
                    "query": "青岚设备的校验码是多少？",
                    "meta": {"search_mode": mode, "final_top_k": 3, "use_reranker": False},
                },
                headers=e2e_headers,
            )
            assert queried.status_code == 200, queried.text
            payload = queried.json()
            assert payload["status"] == "success", payload
            assert any(
                "846213" in hit["content"] and hit["metadata"]["file_id"] == file_id
                for hit in payload["result"]
            ), {"mode": mode, "response": payload}
    finally:
        # 只删除本用例确切创建的任务和知识库，不按公共前缀清理共享环境。
        for task_id in task_ids:
            task_response = await e2e_client.get(f"/api/tasks/{task_id}", headers=e2e_headers)
            assert task_response.status_code == 200, task_response.text
            if task_response.json()["task"]["status"] not in {"success", "failed", "cancelled"}:
                cancelled = await e2e_client.post(f"/api/tasks/{task_id}/cancel", headers=e2e_headers)
                assert cancelled.status_code == 200, cancelled.text
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    task_response = await e2e_client.get(f"/api/tasks/{task_id}", headers=e2e_headers)
                    assert task_response.status_code == 200, task_response.text
                    if task_response.json()["task"]["status"] in {"success", "failed", "cancelled"}:
                        break
                    await asyncio.sleep(1)
                else:
                    pytest.fail(f"清理前须等待文档任务停止：{task_id}")
            deleted_task = await e2e_client.delete(f"/api/tasks/{task_id}", headers=e2e_headers)
            assert deleted_task.status_code == 200, deleted_task.text
        deleted = await e2e_client.delete(f"/api/knowledge/databases/{kb_id}", headers=e2e_headers)
        assert deleted.status_code == 200, deleted.text
        remaining = await e2e_client.get("/api/knowledge/databases", headers=e2e_headers)
        assert remaining.status_code == 200, remaining.text
        assert kb_id not in {item["kb_id"] for item in remaining.json()["databases"]}
