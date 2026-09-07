"""按分块历史来文清单登记可恢复的接入批次。"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import requests

BATCH_ITEM_LIMIT = 200
REQUEST_TIMEOUT_SECONDS = 30


def _chunks(items: list[dict[str, Any]], size: int = BATCH_ITEM_LIMIT) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _post_json(url: str, *, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
    if not response.ok:
        response.raise_for_status()
    return response.json()


def upload_batch(
    *,
    api_base: str,
    token: str,
    source_system: str,
    batch_key: str,
    name: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """创建、登记并提交一个批次；使用相同 batch_key 重跑可安全续传。"""
    if not items:
        raise ValueError("历史来文清单不能为空")
    endpoint = api_base.rstrip("/") + "/api/incoming-documents/ingest-batches"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    batch = _post_json(
        endpoint,
        headers=headers,
        payload={"source_system": source_system, "batch_key": batch_key, "name": name},
    )
    batch_id = str(batch.get("batchId") or "")
    if not batch_id:
        raise ValueError("服务端未返回 batchId")
    registered = 0
    for chunk in _chunks(items):
        _post_json(
            f"{endpoint}/{batch_id}/items",
            headers=headers,
            payload={"source_system": source_system, "items": chunk},
        )
        registered += len(chunk)
    _post_json(f"{endpoint}/{batch_id}/submit", headers=headers, payload={})
    return {"batchId": batch_id, "registered": registered}


def main() -> None:
    parser = argparse.ArgumentParser(description="分块登记历史来文接入任务")
    parser.add_argument(
        "manifest",
        type=Path,
        help="JSON 数组，每项包含 source_document_id、document_metadata、file_manifest",
    )
    parser.add_argument("--api-base", default=os.getenv("INGEST_API_BASE", "http://localhost:5050"))
    parser.add_argument("--token", default=os.getenv("INGEST_TOKEN"), required=os.getenv("INGEST_TOKEN") is None)
    parser.add_argument("--source-system", default="production")
    parser.add_argument("--batch-key", required=True)
    parser.add_argument("--name", default="历史来文导入")
    args = parser.parse_args()
    items = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError("清单必须是 JSON 对象数组")
    result = upload_batch(
        api_base=args.api_base,
        token=args.token,
        source_system=args.source_system,
        batch_key=args.batch_key,
        name=args.name,
        items=items,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
