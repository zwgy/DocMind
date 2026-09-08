"""批量登记历史来文，供外部平台将数据库中的存量来文接入 DocMind。

脚本读取一个 JSON 数组文件，将其中的单份或多份来文提交到
``POST /api/incoming-documents/ingest-batches``。它只负责登记任务；附件由 DocMind
后台 Worker 根据 ``source_url`` 下载，随后异步完成解析、分类和业务信息抽取。

执行 ``python scripts/upload_incoming_batch.py --help`` 可查看完整参数、JSON 示例、
幂等规则和返回状态说明。建议通过 ``INGEST_TOKEN`` 环境变量传递管理员令牌，避免
令牌出现在命令历史或进程参数中。脚本要求 Python 3.12+ 和 requests，可在独立
环境中执行 ``python -m pip install requests`` 安装依赖。
"""

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

USAGE_GUIDE = """
运行条件：
  Python 3.12+，并已安装 requests：
  python -m pip install requests

使用示例：
  # Linux / macOS
  export INGEST_API_BASE="http://docmind.internal:5050"
  export INGEST_TOKEN="管理员访问令牌或 API Key"
  python scripts/upload_incoming_batch.py history.json --source-system oa

  # Windows PowerShell
  $env:INGEST_API_BASE = "http://docmind.internal:5050"
  $env:INGEST_TOKEN = "管理员访问令牌或 API Key"
  python scripts/upload_incoming_batch.py history.json --source-system oa

参数内容：
  documents_file
    本地 UTF-8 JSON 文件路径。文件顶层必须是数组；数组放 1 项就是单条上传，
    放多项就是批量上传。脚本会自动按每批最多 200 份、请求体最多 5 MiB 分块。

  --api-base / INGEST_API_BASE
    DocMind 服务根地址，例如 http://docmind.internal:5050。不要在末尾添加 /api；
    未传参数且未设置环境变量时默认使用 http://localhost:5050。

  --token / INGEST_TOKEN
    由 DocMind 管理员提供的访问令牌或 API Key。推荐使用 INGEST_TOKEN 环境变量，
    避免令牌进入命令历史；未设置环境变量时必须传 --token。

  --source-system
    来源平台的稳定机器标识，长度 1 至 64 字符，例如 oa、archive-system。它不是
    批次名称，生产使用后不要随意更改；幂等身份为
    source_system + source_document_id。

JSON 输入示例（history.json）：
  [
    {
      "source_document_id": "OA-2020-0001",
      "metadata": {
        "title": "关于开展年度检查的通知",
        "document_number": "办发〔2020〕1号",
        "incoming_date": "2020-01-15",
        "source_unit": "办公室",
        "incoming_type": "通知"
      },
      "files": [
        {
          "source_file_id": "OA-FILE-1001",
          "filename": "通知正文.pdf",
          "source_url": "http://oa.internal/files/OA-FILE-1001",
          "is_main_file": true
        },
        {
          "source_file_id": "OA-FILE-1002",
          "filename": "附件.xlsx",
          "source_url": "http://oa.internal/files/OA-FILE-1002",
          "is_main_file": false
        }
      ]
    }
  ]

JSON 字段：
  source_document_id  必填；来文在来源平台中的稳定唯一 ID，最长 256 字符。
  metadata            可选对象；建议提供 title、document_number、incoming_date
                      （YYYY-MM-DD）、source_unit、incoming_type，其他业务字段也可保留。
  files               必填非空数组；一次提交该来文的完整主文件和附件清单。
  source_file_id      必填；附件在该来文内的稳定唯一 ID，最长 512 字符。
  filename            必填；带受支持扩展名的原始文件名，例如 .pdf、.docx、.xlsx。
  source_url          必填；Worker 可长期直接访问的 HTTP/HTTPS 下载地址，不能依赖
                      浏览器 Cookie、临时登录态，也不能在 URL 中嵌入用户名和密码；
                      单个下载文件不能超过 100 MiB。
  is_main_file        可选布尔值；正文建议设为 true，普通附件设为 false；一份来文
                      最多只能有一个主文件。

输出状态：
  accepted / exists / requeued / conflict
  accepted：新任务已登记，等待定时调度，不代表附件已经处理完成。
  exists：完全相同的任务已存在，属于幂等成功，不会重复创建或重复执行。
  requeued：原任务此前失败或取消，现已重新进入待处理状态。
  conflict：同一幂等身份对应的 metadata 或 files 不同；脚本以退出码 1 结束，
            不会覆盖原任务。

网络中断或响应丢失时，使用完全相同的文件和参数重新执行即可。处理进度与失败原因
可在 DocMind“来文管理 -> 处理任务”中查看。

脚本向标准输出打印一个 JSON 对象：items 是逐份来文结果，summary 是各状态数量，
conflictSourceDocumentIds 是发生输入冲突的来源来文 ID。没有冲突时退出码为 0；存在
conflict 时退出码为 1；参数、文件、网络或 HTTP 错误也会以非零状态退出并打印错误。
"""


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
    parser = argparse.ArgumentParser(
        description="将单份或多份历史来文登记为 DocMind 后台处理任务",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=USAGE_GUIDE,
    )
    parser.add_argument(
        "documents_file",
        type=Path,
        help="UTF-8 JSON 数组文件；每项包含 source_document_id、metadata、files，详见下方示例",
    )
    parser.add_argument(
        "--api-base",
        default=os.getenv("INGEST_API_BASE", "http://localhost:5050"),
        help="DocMind 服务根地址，不含 /api；也可设置 INGEST_API_BASE（默认：%(default)s）",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("INGEST_TOKEN"),
        required=os.getenv("INGEST_TOKEN") is None,
        help="管理员访问令牌或 API Key；推荐设置 INGEST_TOKEN 环境变量",
    )
    parser.add_argument(
        "--source-system",
        required=True,
        help="来源平台的稳定机器标识，例如 oa；与 source_document_id 共同构成幂等身份",
    )
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
