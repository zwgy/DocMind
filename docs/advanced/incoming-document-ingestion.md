# 来文上传接口接入指南

外部业务系统在收到上级来文时，可以使用 API Key 主动调用 DocMind 来文上传接口。上传成功后，DocMind 会异步完成文件解析、来文摘要和结构化信息抽取；用户随后打开嵌入式小助手时，可以直接读取已经生成的来文结果。

接口同时支持两种上传方式：

- **JSON 下载地址方式**：外部系统提供附件下载地址，由 DocMind 后端直接下载。适合 DocMind 服务器能够访问附件服务的场景，优先推荐。
- **multipart 文件方式**：外部系统读取附件内容后直接上传给 DocMind。适合 DocMind 无法访问附件下载地址的场景。

两种方式调用同一个接口，并使用相同的来文和附件身份规则。

## 接口信息

生产环境的完整地址通常为：

```text
POST /api/incoming-documents/ingest
DOCMIND_API_KEY = yxkey_<请向 DocMind 管理员申请>
DOCMIND_BASE_URL = https://192.168.1.220:5174
```

API Key 由 DocMind 管理员创建，必须放在服务端配置或密钥管理系统中。不要把 API Key 写入浏览器代码、网页源码或 URL。API Key 的创建和认证规则见 [API Key 外部集成](./api-key-integration.md)。

## 身份规则

来文与附件使用以下唯一身份：

```text
来文：source_system + source_doc_id
附件：source_system + source_doc_id + source_file_id
```

字段语义：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `source_system` | 否 | 嵌入系统的稳定标识，默认 `production`。建议显式传入，例如 `oa` |
| `document_metadata.source_doc_id` | 是 | 来源系统中的来文 ID；同一 `source_system` 内必须唯一 |
| `source_file_id` | 是 | 当前来文下的附件 ID；同一来文内必须唯一 |

重复提交相同身份、相同内容和相同元数据时，DocMind 会复用已有记录，不会生成重复来文。相同身份下附件内容或元数据发生变化时，DocMind 会按新内容重新处理；如果来文正在解析，调用方应等待当前任务结束后再重试。

## 方式一：JSON 下载地址

当 DocMind Worker 能够访问附件下载地址时，推荐使用这种方式。附件由服务器之间直接传输，不经过用户浏览器，因此不受浏览器 CORS 限制。请求成功只登记持久化接入任务；文件下载在专用来文 Worker 中执行，不会占用 API 请求连接。

### 请求示例

```json
{
  "source_system": "oa",
  "document_metadata": {
    "source_doc_id": "37908",
    "document_number": "上铁辆〔2020〕316号",
    "title": "关于重新印发路用客车检修运用管理办法的通知",
    "incoming_type": "集团公司文件",
    "source_unit": "安全科",
    "incoming_date": "2020-10-20"
  },
  "files": [
    {
      "source_file_id": "202608110001",
      "filename": "上铁辆〔2020〕316号.pdf",
      "source_url": "http://attachments.example/Attachment/Download?fileid=202608110001",
      "is_main_file": true
    },
    {
      "source_file_id": "202608110002",
      "filename": "附件1.xlsx",
      "source_url": "http://attachments.example/Attachment/Download?fileid=202608110002",
      "is_main_file": false
    }
  ]
}
```

### curl 示例

```bash
curl -X POST "$DOCMIND_BASE_URL/api/incoming-documents/ingest" \
  -H "Authorization: Bearer $DOCMIND_API_KEY" \
  -H "Content-Type: application/json" \
  --data-binary @incoming-document.json
```

JSON 文件和请求体必须使用 UTF-8 编码；`source_url` 中包含中文文件名时，建议按 URL 规范进行百分号编码。否则后端可能收到乱码路径，表现为附件服务器返回 `403` 或 `404`。

### Python 示例

```python
import os

import requests


base_url = os.environ["DOCMIND_BASE_URL"].rstrip("/")
api_key = os.environ["DOCMIND_API_KEY"]

payload = {
    "source_system": "oa",
    "document_metadata": {
        "source_doc_id": "37908",
        "document_number": "上铁辆〔2020〕316号",
        "title": "关于重新印发路用客车检修运用管理办法的通知",
        "incoming_type": "集团公司文件",
        "source_unit": "安全科",
        "incoming_date": "2020-10-20",
    },
    "files": [
        {
            "source_file_id": "202608110001",
            "filename": "上铁辆〔2020〕316号.pdf",
            "source_url": "http://attachments.example/Attachment/Download?fileid=202608110001",
            "is_main_file": True,
        }
    ],
}

response = requests.post(
    f"{base_url}/api/incoming-documents/ingest",
    headers={"Authorization": f"Bearer {api_key}"},
    json=payload,
    timeout=90,
)
response.raise_for_status()
print(response.json())
```

`source_url` 必须满足以下要求：

- 使用绝对 `http://` 或 `https://` 地址。
- DocMind 后端服务器能够访问该地址。
- 下载接口直接返回文件内容，而不是登录页或其他 HTML 页面。
- 单个文件不超过 100 MB。
- 下载应在 Worker 的下载超时内完成。

如果附件下载接口需要 Cookie、动态登录态或自定义请求头，当前 JSON 方式无法携带这些凭证，应改用 multipart 文件方式。

## 方式二：multipart 文件上传

当附件地址仅能由嵌入系统访问，或下载接口需要嵌入系统自己的登录态时，可以由嵌入系统后端读取文件后，通过 multipart 直接上传。

### 表单字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `source_system` | string | 否 | 来源系统标识，默认 `production` |
| `document_metadata` | JSON string | 是 | 来文公共元数据，必须包含 `source_doc_id` |
| `file_metas` | JSON array string | 是 | 与 `files` 顺序一一对应的附件元数据 |
| `files` | file，可重复 | 是 | 附件二进制内容，单个文件不超过 100 MB |

`file_metas` 中每一项包含：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `source_file_id` | 是 | 来源系统中的附件 ID |
| `filename` | 是 | 文件名，建议包含正确扩展名 |
| `is_main_file` | 否 | 是否为主文件；一份来文最多一个主文件 |

### 常用文件 Content-Type

multipart 上传时，建议在文件字段的 `type=` 中声明真实文件类型。DocMind 主要根据文件内容和扩展名解析，正确的 Content-Type 仍有助于网关、日志和其他 HTTP 客户端识别文件。

| 文件格式 | Content-Type | curl 文件字段示例 |
| --- | --- | --- |
| PDF | `application/pdf` | `-F 'files=@来文.pdf;type=application/pdf'` |
| Word `.doc` | `application/msword` | `-F 'files=@附件.doc;type=application/msword'` |
| Word `.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | `-F 'files=@附件.docx;type=application/vnd.openxmlformats-officedocument.wordprocessingml.document'` |
| Excel `.xls` | `application/vnd.ms-excel` | `-F 'files=@附件.xls;type=application/vnd.ms-excel'` |
| Excel `.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | `-F 'files=@附件.xlsx;type=application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'` |
| PowerPoint `.ppt` | `application/vnd.ms-powerpoint` | `-F 'files=@附件.ppt;type=application/vnd.ms-powerpoint'` |
| PowerPoint `.pptx` | `application/vnd.openxmlformats-officedocument.presentationml.presentation` | `-F 'files=@附件.pptx;type=application/vnd.openxmlformats-officedocument.presentationml.presentation'` |
| ZIP | `application/zip` | `-F 'files=@附件.zip;type=application/zip'` |
| 无法确定的二进制文件 | `application/octet-stream` | `-F 'files=@附件.bin;type=application/octet-stream'` |

不要为了统一而全部填写 `application/octet-stream`；已知格式时应使用上表中的具体类型。

### curl 示例

```bash
curl -X POST "$DOCMIND_BASE_URL/api/incoming-documents/ingest" \
  -H "Authorization: Bearer $DOCMIND_API_KEY" \
  -F 'source_system=oa' \
  -F 'document_metadata={"source_doc_id":"37908","document_number":"上铁辆〔2020〕316号","title":"关于重新印发路用客车检修运用管理办法的通知","incoming_type":"集团公司文件","source_unit":"安全科","incoming_date":"2020-10-20"}' \
  -F 'file_metas=[{"source_file_id":"202608110001","filename":"上铁辆〔2020〕316号.pdf","is_main_file":true},{"source_file_id":"202608110002","filename":"附件1.xlsx","is_main_file":false}]' \
  -F 'files=@上铁辆〔2020〕316号.pdf;type=application/pdf' \
  -F 'files=@附件1.xlsx;type=application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
```

### Python 示例

```python
import json
import os

import requests


base_url = os.environ["DOCMIND_BASE_URL"].rstrip("/")
api_key = os.environ["DOCMIND_API_KEY"]

document_metadata = {
    "source_doc_id": "37908",
    "document_number": "上铁辆〔2020〕316号",
    "title": "关于重新印发路用客车检修运用管理办法的通知",
    "incoming_type": "集团公司文件",
    "source_unit": "安全科",
    "incoming_date": "2020-10-20",
}
file_metas = [
    {
        "source_file_id": "202608110001",
        "filename": "上铁辆〔2020〕316号.pdf",
        "is_main_file": True,
    },
    {
        "source_file_id": "202608110002",
        "filename": "附件1.xlsx",
        "is_main_file": False,
    },
]

with (
    open("上铁辆〔2020〕316号.pdf", "rb") as main_file,
    open("附件1.xlsx", "rb") as attachment,
):
    response = requests.post(
        f"{base_url}/api/incoming-documents/ingest",
        headers={"Authorization": f"Bearer {api_key}"},
        data={
            "source_system": "oa",
            "document_metadata": json.dumps(document_metadata, ensure_ascii=False),
            "file_metas": json.dumps(file_metas, ensure_ascii=False),
        },
        files=[
            ("files", ("上铁辆〔2020〕316号.pdf", main_file, "application/pdf")),
            (
                "files",
                (
                    "附件1.xlsx",
                    attachment,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            ),
        ],
        timeout=120,
    )

response.raise_for_status()
print(response.json())
```

`file_metas` 数组和重复的 `files` 字段必须数量相同、顺序一致。不要手工设置 multipart 的 `Content-Type` 请求头，HTTP 客户端会自动生成包含 boundary 的正确请求头。

上述 curl 写法面向 Bash。Windows PowerShell 调用原生 `curl.exe` 时，内联 JSON 的双引号可能被命令行参数解析移除；建议改用本节 Python 示例，或把 `document_metadata`、`file_metas` 分别写入 UTF-8 JSON 文件后以 `-F "document_metadata=<metadata.json"`、`-F "file_metas=<file-metas.json"` 传入。

## 成功响应

首次接收或内容发生变化时，接口返回类似：

```json
{
  "incomingId": "inc_0123456789abcdef",
  "taskId": "task_0123456789abcdef",
  "status": "accepted",
  "items": [
    {
      "sourceFileId": "202608110001",
      "status": "accepted",
      "isMainFile": true
    }
  ],
  "fileCount": 1
}
```

`status: accepted` 表示接入任务已登记并已提交后台解析，不代表 JSON 下载已经完成，也不代表摘要已经生成。multipart 文件会先被接收到对象存储，再由后台解析；JSON 下载由专用 Worker 在开始执行后访问 `source_url`。解析完成后，小助手会通过来文查询接口读取最新状态和摘要。

重复提交完全相同的已处理来文时，接口可能返回已有来文状态、`taskId: null`，附件状态为 `exists`。调用方应把这类响应视为幂等成功。

## 常见错误

| HTTP 状态 | 常见原因 | 处理建议 |
| --- | --- | --- |
| `400` | 缺少 `document_metadata.source_doc_id` | 把来文 ID 放入 `document_metadata`，不要放在顶层或独立表单字段 |
| `400` | 传入 `source_function_id`、`business_id` 或外部用户字段 | 删除这些页面和用户上下文字段 |
| `400` | `file_metas` 与 `files` 数量不一致 | 保证数组数量和顺序与文件字段完全一致 |
| `400` | 同时声明多个主文件 | 一份来文最多设置一个 `is_main_file: true` |
| `400` | 来文正在处理 | 等待当前解析完成后再重试 |
| `400` | JSON 请求字段、下载地址格式或附件元数据不合法 | 修正请求后重试 |
| `401` | API Key 无效、过期或格式错误 | 检查 `Authorization: Bearer <API Key>` |

## 接入检查清单

- 使用稳定的 `source_system`，生产环境上线后不要随意修改。
- 确保 `source_doc_id` 在该来源系统内唯一。
- 确保 `source_file_id` 在同一来文内唯一。
- 多附件一次性提交，并正确标识唯一主文件。
- JSON 方式上线前，从 DocMind 服务器验证每个 `source_url` 可以直接下载。
- 将 API Key 保存在嵌入系统后端，不暴露给浏览器。
- 把 `accepted` 当作异步任务已受理，不当作解析完成。

## 历史来文批量登记

历史来文使用管理员接口和仓库脚本 `scripts/upload_incoming_batch.py` 登记；任务元数据保存在 PostgreSQL，文件原件、附件、Markdown 和解析图片保存在 MinIO。Redis 只承载已被 Dispatcher 领取的 `job_id + delivery_token` 短消息，因此重启或消息过期不会丢失待处理任务。

脚本读取包含来源来文 ID、元数据和长期有效下载地址的 JSON 清单，每次登记 200 项。同一 `--batch-key` 可以在网络中断后重复执行，已经成功登记的项会复用而不会重复创建任务。先完成登记，再提交批次：

```bash
python scripts/upload_incoming_batch.py \
  ./history-2025.json \
  --api-base "$DOCMIND_BASE_URL" \
  --token "$DOCMIND_API_KEY" \
  --source-system oa \
  --batch-key history-2025 \
  --name "2025 年历史来文"
```

历史任务仅在上海时区 18:00（含）至次日 07:30（不含）开始；07:30 后不会启动新的历史任务，已运行的任务允许自然完成。即时来文不受此时间窗口限制，并优先于尚未开始的历史任务。历史来源的下载接口必须可由 Worker 长期访问，不能依赖浏览器 Cookie 或短期登录态。

管理员可在“来文管理”的“接入任务”区域查看 PostgreSQL 中的任务概览、状态、优先级、阶段、重试次数和错误摘要，并将尚未完成的历史任务提升为即时优先级。页面不显示 Redis 队列内容，也不会暴露完整源站下载地址。
