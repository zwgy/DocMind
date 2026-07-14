"""
来文文档上传脚本

设计原则
========

- 单一事实源：``file_metas`` 与 ``files`` 均由 ``UPLOAD_ITEMS`` 派生。
- 本地校验：上传前逐项校验字段非空、ID/文件名不重复、文件存在。
- 幂等与重试：后端按 ``source_file_id`` 去重；4xx 立即抛出，5xx 与
  连接异常做有限次退避重试。
- 可观测：关键步骤通过 ``logging`` 输出。
- 可配置：API 地址与访问令牌通过环境变量覆盖。

配置项
------

- ``INGEST_API_BASE``：API base 地址，默认 ``http://192.168.1.220:5050``。
- ``INGEST_TOKEN``：访问令牌，默认值仅为示例，生产部署前必须替换。
- ``BASE_DIR``：第三方本地的来文目录，按实际情况改写。
- ``UPLOAD_ITEMS``：待上传清单，``source_file_id`` 与 ``filename``
  一一对应。

文件命名建议
------------

本文件当前名称带 ``.py.py`` 后缀（疑似复制/重命名失误），建议改为
``ingest_incoming_document.py``。Python 解释器可正常执行当前文件名，
但保留双后缀对第三方集成方不友好。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import requests

# =====================================================================
# 配置（演示用：部署到生产前必须替换为环境变量或外部配置）
# =====================================================================

# API 地址：第三方按实际部署的网关地址替换，或通过 INGEST_API_BASE 注入。
DEFAULT_API_BASE = "http://192.168.1.220:5050"

# 访问令牌：示例值仅供本地联调，生产环境通过 INGEST_TOKEN 注入。
DEFAULT_TOKEN = "yxkey_658c6cb0d8ae81385b89fb1bdacefc316fbcf23ae123984b"

# 单次 HTTP 请求的超时秒数。
REQUEST_TIMEOUT_SECONDS = 60

# 网络层重试次数（不含首次）。
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2

# 第三方本地的来文目录，按实际情况替换。
BASE_DIR = Path(
    r"D:\work\llm\docMind\branches\docMind-dev-v1.0\chat-iframe\public\中国铁路上海局集团有限公司关于重新印发《中国铁路上海局集团有限公司路用客车检修运用管理办法》的通知"
)

# 接口路径，固定不需要外部配置。
INGEST_ENDPOINT = "/api/incoming-documents/ingest"

# =====================================================================
# 待上传清单（单一事实源）
# =====================================================================


@dataclass(frozen=True)
class UploadItem:
    """待上传清单的最小单元。

    Attributes:
        source_file_id: 第三方系统内的文件唯一 ID，接口按此字段做幂等去重。
        filename: 文件名，作为 multipart ``files`` 字段的 filename 与
            Yuxi 详情页展示名。
    """

    source_file_id: str
    filename: str


# 待上传清单：顺序即 multipart 提交时的顺序，后端按 zip(strict=True)
# 与 file_metas 严格对齐。
UPLOAD_ITEMS: tuple[UploadItem, ...] = (
    UploadItem(source_file_id="202010200206", filename="上铁辆〔2020〕316号.pdf"),
    UploadItem(source_file_id="202010200207", filename="附件4.xls"),
    UploadItem(source_file_id="202010200208", filename="附件5.doc"),
)

# 来文元数据：multipart 普通字段，仅 source_doc_id / source_function_id 必填，
# 其他字段缺失视为 None。
#
#   source_system       系统
#   source_function_id  功能 id
#   source_doc_id       业务 id / 来文 id（必填，幂等键）
#   document_number     来文编号
#   title               来文标题
#   incoming_type       来文类别
#   source_unit         来文单位
#   incoming_date       来文日期 YYYY-MM-DD
INGEST_METADATA: dict[str, str] = {
    "source_system": "oa",
    "source_function_id": "incomingDocument",
    "source_doc_id": "37908",
    "document_number": "上铁辆〔2020〕316号",
    "title": "中国铁路上海局集团有限公司关于重新印发《中国铁路上海局集团有限公司路用客车检修运用管理办法》的通知",
    "incoming_type": "集团公司文件",
    "source_unit": "安全科",
    "incoming_date": "2020-10-20",
}

# =====================================================================
# 本地校验与字段构造
# =====================================================================


def validate_upload_plan(
    items: Sequence[UploadItem], base_dir: Path
) -> list[Path]:
    """上传前自检：清单非空、字段非空、ID/文件名不重复、文件真实存在。

    Returns:
        与 ``items`` 等长、按 ``items`` 顺序排列的文件绝对路径列表。
    """
    if not items:
        raise ValueError("UPLOAD_ITEMS 不能为空，至少需要一条待上传记录")

    seen_ids: set[str] = set()
    seen_files: set[str] = set()
    paths: list[Path] = []
    for index, item in enumerate(items, start=1):
        source_file_id = item.source_file_id.strip()
        filename = item.filename.strip()
        if not source_file_id:
            raise ValueError(f"第 {index} 条记录缺少 source_file_id")
        if not filename:
            raise ValueError(f"第 {index} 条记录缺少 filename")
        if source_file_id in seen_ids:
            raise ValueError(f"source_file_id 重复: {source_file_id}")
        if filename in seen_files:
            raise ValueError(f"filename 重复: {filename}")
        seen_ids.add(source_file_id)
        seen_files.add(filename)

        path = base_dir / item.filename
        if not path.is_file():
            raise FileNotFoundError(
                f"第 {index} 条记录对应文件不存在: {path}"
            )
        paths.append(path)

    return paths


def build_file_metas(items: Sequence[UploadItem]) -> str:
    """根据清单派生 ``file_metas`` 字段：JSON 数组字符串。

    服务端会校验数组长度必须等于 ``files`` 字段数量（``zip(strict=True)``）。
    """
    payload = [
        {"source_file_id": item.source_file_id, "filename": item.filename}
        for item in items
    ]
    return json.dumps(payload, ensure_ascii=False)


# =====================================================================
# HTTP 上传
# =====================================================================

logger = logging.getLogger(__name__)


def upload(
    *,
    api_base: str,
    token: str,
    base_dir: Path,
    items: Sequence[UploadItem],
    metadata: dict[str, str],
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    max_retries: int = MAX_RETRIES,
) -> dict[str, Any]:
    """提交来文与元数据到 ``/api/incoming-documents/ingest``。

    4xx 立即抛出；5xx 与网络异常做有限次退避重试。

    Args:
        api_base: API base 地址。
        token: 访问令牌。
        base_dir: 第三方本地的来文目录。
        items: 待上传清单。
        metadata: 来文元数据，作为 multipart 普通字段提交。
        timeout: 单次 HTTP 请求的超时秒数。
        max_retries: 重试次数（不含首次）。
    """
    paths = validate_upload_plan(items, base_dir)
    file_metas = build_file_metas(items)

    form_data = {**metadata, "file_metas": file_metas}
    endpoint = api_base.rstrip("/") + INGEST_ENDPOINT
    headers = {"Authorization": f"Bearer {token}"}

    with ExitStack() as stack:
        # 同一份 items 派生 files，顺序与 items 严格一致。
        files_payload = [
            (
                "files",
                (
                    item.filename,
                    stack.enter_context(path.open("rb")),
                ),
            )
            for item, path in zip(items, paths, strict=True)
        ]

        last_exc: requests.RequestException | None = None
        total_attempts = max_retries + 1
        for attempt in range(1, total_attempts + 1):
            logger.info(
                "提交来文 attempt=%d/%d files=%d source_doc_id=%s",
                attempt,
                total_attempts,
                len(items),
                metadata.get("source_doc_id"),
            )
            started = time.monotonic()
            try:
                response = requests.post(
                    endpoint,
                    headers=headers,
                    data=form_data,
                    files=files_payload,
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                # 网络层异常——进入重试。
                last_exc = exc
                logger.warning("网络异常: %s", exc)
            else:
                elapsed = time.monotonic() - started
                if response.ok:
                    logger.info(
                        "提交成功 status=%d 耗时=%.2fs response=%s",
                        response.status_code,
                        elapsed,
                        response.text,
                    )
                    return response.json()

                # 4xx 是请求本身的问题，重试无意义。
                if 400 <= response.status_code < 500:
                    logger.error(
                        "请求被服务端拒绝 status=%d body=%s",
                        response.status_code,
                        response.text,
                    )
                    response.raise_for_status()

                last_exc = requests.HTTPError(
                    f"status={response.status_code}, body={response.text}"
                )
                logger.warning(
                    "服务端异常 status=%d body=%s，准备重试",
                    response.status_code,
                    response.text,
                )

            if attempt <= max_retries:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

        assert last_exc is not None  # 循环出口必有异常
        raise last_exc


# =====================================================================
# 入口
# =====================================================================


def main() -> int:
    """脚本入口。

    Returns:
        进程退出码：0=成功，1=网络/服务端错误，2=本地校验错误。
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )

    api_base = os.environ.get("INGEST_API_BASE", DEFAULT_API_BASE)
    token = os.environ.get("INGEST_TOKEN", DEFAULT_TOKEN)
    if token == DEFAULT_TOKEN:
        logger.warning(
            "使用默认 TOKEN，请在生产环境通过 INGEST_TOKEN 环境变量覆盖，"
            "并避免把生产令牌提交到代码仓库"
        )

    logger.info("开始提交来文 API=%s 文件数=%d", api_base, len(UPLOAD_ITEMS))
    try:
        result = upload(
            api_base=api_base,
            token=token,
            base_dir=BASE_DIR,
            items=UPLOAD_ITEMS,
            metadata=INGEST_METADATA,
        )
    except (FileNotFoundError, ValueError) as exc:
        logger.error("本地校验失败: %s", exc)
        return 2
    except requests.RequestException as exc:
        logger.error("提交失败: %s", exc)
        return 1
    logger.info("来文处理完成: %s", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())