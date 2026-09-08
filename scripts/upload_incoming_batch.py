"""将历史来文按接口限制分块登记为可恢复任务。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import requests

BATCH_ITEM_LIMIT = 200
BATCH_BYTES_LIMIT = 5 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 30
RESULT_STATUSES = ("accepted", "exists", "requeued", "conflict")


def _encode_payload(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _chunks(source_system: str, documents: list[dict[str, Any]]):
    chunk: list[dict[str, Any]] = []
    for document in documents:
        candidate = [*chunk, document]
        payload = {"source_system": source_system, "documents": candidate}
        if chunk and (len(candidate) > BATCH_ITEM_LIMIT or len(_encode_payload(payload)) > BATCH_BYTES_LIMIT):
            yield chunk
            chunk = []
            payload = {"source_system": source_system, "documents": [document]}
        if len(_encode_payload(payload)) > BATCH_BYTES_LIMIT:
            source_document_id = str(document.get("source_document_id") or "")
            raise ValueError(f"来文 {source_document_id} 单项请求超过 5 MiB")
        chunk.append(document)
    if chunk:
        yield chunk


def _post_json(url: str, *, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(url, headers=headers, data=_encode_payload(payload), timeout=REQUEST_TIMEOUT_SECONDS)
    if not response.ok:
        response.raise_for_status()
    return response.json()


def upload_batch(
    *,
    api_base: str,
    token: str,
    source_system: str,
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    """逐块登记历史任务；网络失败后原样重跑由文档身份保证幂等。"""
    if not documents:
        raise ValueError("历史来文清单不能为空")
    endpoint = api_base.rstrip("/") + "/api/incoming-documents/ingest-batches"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    results: list[dict[str, Any]] = []
    for chunk in _chunks(source_system, documents):
        response = _post_json(
            endpoint,
            headers=headers,
            payload={"source_system": source_system, "documents": chunk},
        )
        items = response.get("items")
        if not isinstance(items, list) or len(items) != len(chunk):
            raise ValueError("服务端逐项结果数量与请求不一致")
        for item in items:
            if not isinstance(item, dict) or item.get("status") not in RESULT_STATUSES:
                raise ValueError("服务端返回了未知的登记状态")
        results.extend(items)

    summary = {status: sum(item["status"] == status for item in results) for status in RESULT_STATUSES}
    return {
        "items": results,
        "summary": summary,
        "conflictSourceDocumentIds": [
            str(item.get("sourceDocumentId") or "") for item in results if item["status"] == "conflict"
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="分块登记历史来文接入任务")
    parser.add_argument(
        "documents_file",
        type=Path,
        help="JSON 数组，每项包含 source_document_id、metadata、files",
    )
    parser.add_argument("--api-base", default=os.getenv("INGEST_API_BASE", "http://localhost:5050"))
    parser.add_argument("--token", default=os.getenv("INGEST_TOKEN"), required=os.getenv("INGEST_TOKEN") is None)
    parser.add_argument("--source-system", required=True)
    args = parser.parse_args()
    documents = json.loads(args.documents_file.read_text(encoding="utf-8"))
    if not isinstance(documents, list) or not all(isinstance(item, dict) for item in documents):
        raise ValueError("清单必须是 JSON 对象数组")
    result = upload_batch(
        api_base=args.api_base,
        token=args.token,
        source_system=args.source_system,
        documents=documents,
    )
    print(json.dumps(result, ensure_ascii=False))
    if result["summary"]["conflict"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
