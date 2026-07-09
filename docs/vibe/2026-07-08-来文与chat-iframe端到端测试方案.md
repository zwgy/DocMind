# 来文与 chat-iframe 端到端测试方案

## 目标

验证部署后这几条主链路可用：

1. 外部系统直接调用 `/api/incoming-documents/ingest` 上传来文，后台保存原文、解析 Markdown、生成摘要、执行业务结构化抽取，但不自动进入知识库。
2. 外部系统未预上传来文时，`chat-iframe` 选择页面附件后自动调用 `/api/incoming-documents/ingest`，附件处于同步/解析中时回答不能编造内容。
3. 来文解析完成后，`chat-iframe` 能把摘要和可读取全文的路径注入运行上下文；摘要不足时模型应调用 `read_file` 读取附件 Markdown，问网页时也按同样规则读取长网页全文。
4. 不同业务 ID 的聊天列表按 `source_system:function_id:business_id` 隔离。

## 测试前置条件

- 已部署 API、web、chat-iframe、worker、PostgreSQL、MinIO。
- worker 正常消费 Tasker 任务。
- `BUSINESS_EXTRACTION_MODEL` 或 `DEFAULT_MODEL` 可调用本地模型。
- 测试用户能访问 chat-iframe 和 `/api/incoming-documents/*`。
- 管理员能访问 Web「来文管理」。
- 测试时必须明确来源标识一致性：后端按 `source_system + source_document_id + source_file_id` 做单文件幂等，iframe 摘要查询默认使用附件里的 `sourceSystem/sourceKey`，附件未带 `sourceSystem` 时后端按 `production` 查询。外部系统预上传若使用 `source_system=oa`，iframe 附件 payload 也必须带 `sourceSystem=oa`，否则查询会命中不到。
- 准备 3 个真实附件：
  - `incoming-risk-001.pdf`：包含明确风险、整改要求、责任部门。
  - `incoming-summary-002.docx`：包含摘要可回答的问题，也包含需要全文细节的问题。
  - `incoming-web-page-long.html` 或一段超过 8000 字的网页正文，用于验证网页 `read_file`。

## 认证准备

### 方式 A：Web 登录后取 Token

1. 打开部署后的 Web。
2. 使用管理员或测试用户登录。
3. 从浏览器开发者工具中取当前请求的 `Authorization: Bearer <token>`。

### 方式 B：使用接口登录

按当前部署认证接口获取 token。后续示例统一使用：

```bash
export DOCMIND_API="https://你的域名/api"
export DOCMIND_TOKEN="Bearer <token>"
```

Windows PowerShell 可使用：

```powershell
$env:DOCMIND_API = "https://你的域名/api"
$env:DOCMIND_TOKEN = "Bearer <token>"
```

## 场景一：外部系统直接上传来文

### 1.1 multipart 多文件上传模式

当前只支持外部系统直接把文件内容推给 docMind；暂不支持 URL 拉取模式。上传后原始文件直接保存到 MinIO，解析后的 Markdown 也保存到 MinIO，PostgreSQL 只保存文件地址、来文元数据、处理状态、摘要和入库关联信息。

必填字段：

- `source_doc_id`：来文 ID，对应外部系统 `ID`。
- `files`：一个或多个文件二进制字段，可重复出现。
- `file_metas`：JSON 数组，长度必须与 `files` 数量一致。每项必须包含：
  - `source_file_id`：外部系统给该文件的独立编号。
  - `filename`：真实文件名，必须保留扩展名。

建议字段：

- `source_system`：外部系统编码，例如 `oa`；不传默认为 `production`。
- `document_number`：来文文号，对应外部系统 `lwwh`。
- `title`：来文标题。
- `incoming_type`：来文类别，对应外部系统 `lwtype`。
- `source_unit`：来文单位，对应外部系统 `lwdw`。
- `incoming_date`：来文日期，对应外部系统 `lwrq`。

主文件判断规则：

- `filename` 等于 `document_number`，或去掉扩展名后等于 `document_number`，则该文件标记为主文件。
- 未传 `document_number` 时，文件列表中的第一个文件标记为主文件。
- 其他文件标记为附件。

请求示例：

```bash
curl -X POST "$DOCMIND_API/incoming-documents/ingest" \
  -H "Authorization: $DOCMIND_TOKEN" \
  -F "source_doc_id=doc-risk-001" \
  -F "document_number=来文〔2026〕1号" \
  -F "title=风险整改来文" \
  -F "incoming_type=安全管理" \
  -F "source_unit=安监部" \
  -F "incoming_date=2026-07-09" \
  -F "source_system=oa" \
  -F "files=@./来文〔2026〕1号.pdf" \
  -F "files=@./附件1-整改清单.xlsx" \
  -F 'file_metas=[
    {"source_file_id":"file-001","filename":"来文〔2026〕1号.pdf"},
    {"source_file_id":"file-002","filename":"附件1-整改清单.xlsx"}
  ]'
```

期望响应：

```json
{
  "status": "accepted",
  "sourceDocId": "doc-risk-001",
  "items": [
    {
      "incomingId": "inc_xxx",
      "taskId": "task_xxx",
      "status": "accepted",
      "sourceFileId": "file-001",
      "filename": "来文〔2026〕1号.pdf",
      "isMainFile": true,
      "knowledgeImportStatus": "none"
    },
    {
      "incomingId": "inc_yyy",
      "taskId": "task_yyy",
      "status": "accepted",
      "sourceFileId": "file-002",
      "filename": "附件1-整改清单.xlsx",
      "isMainFile": false,
      "knowledgeImportStatus": "none"
    }
  ]
}
```

### 1.2 结果检查

1. 打开 Web「来文管理」。
2. 搜索 `来文〔2026〕1号`、`doc-risk-001` 或 `file-001`。
3. 检查列表字段：
   - 状态最终为 `ready`。
   - 分类有值。
   - 入库状态为 `none`。
   - 来文文号、标题、类别、单位、日期有值。
   - 主文件 `isMainFile=true`，附件 `isMainFile=false`。
4. 打开详情：
   - 原文地址存在。
   - Markdown 预览存在。
   - 摘要存在。
   - 结构化结果存在或为空对象，但接口不能报错。
5. 查询接口检查：

```bash
curl -X POST "$DOCMIND_API/incoming-documents/extractions/query" \
  -H "Authorization: $DOCMIND_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "files": [
      {
        "id": "file-001",
        "name": "来文〔2026〕1号.pdf",
        "sourceKey": "file-001",
        "sourceDocId": "doc-risk-001",
        "sourceSystem": "oa"
      }
    ]
  }'
```

期望：

- `matchStatus = matched`
- `processingStatus = ready`
- `extractionStatus = ready`
- `summary` 非空
- `hasMarkdown = true`
- `knowledgeImportStatus = none`

## 场景二：example.html 增加真实附件并验证自动上传

### 2.1 准备真实附件 URL

把真实测试附件放到部署环境可被 API 下载的位置，推荐两种：

- 放到外部系统真实下载地址。
- 放到 chat-iframe 静态目录的测试文件目录，例如 `/chat-iframe/example-files/incoming-risk-001.pdf`。

要求：

- API 容器能访问该 URL。
- URL 不需要浏览器登录态，或下载服务允许后端访问。
- 文件扩展名在支持范围内：`pdf/doc/docx/xls/xlsx/ppt/pptx/txt/md/csv/html`。

### 2.2 修改 example.html 的附件区

在 `chat-iframe/public/example.html` 的 `.items` 下增加真实附件：

```html
<div class="item" id="real-risk-001_BOX" attachment="real-risk-001">
  <a
    href="/chat-iframe/example-files/incoming-risk-001.pdf?real-risk-001"
    onclick="YZSoft.File.download('/chat-iframe/example-files/incoming-risk-001.pdf?real-risk-001'); return false"
  >
    <span class="flag"></span>
    "incoming-risk-001.pdf"
    <span class="size">-512KB</span>
  </a>
</div>
```

父页面脚本会抽取：

- `name = incoming-risk-001.pdf`
- `sourceUrl = /chat-iframe/example-files/incoming-risk-001.pdf?real-risk-001`
- `sourceKey = real-risk-001`
- `id = real-risk-001`

如果该附件用于匹配外部系统已经预上传过的来文，需确保传给 iframe 的附件对象包含相同 `sourceSystem`。可在接入方调用 `chat.setFiles()` 时显式传入：

```js
chat.setFiles([
  {
    id: 'real-risk-001',
    name: 'incoming-risk-001.pdf',
    sourceUrl: '/chat-iframe/example-files/incoming-risk-001.pdf?real-risk-001',
    sourceKey: 'real-risk-001',
    sourceSystem: 'oa'
  }
])
```

### 2.3 验证未预上传时自动同步

1. 部署后打开：

```text
https://你的域名/chat-iframe/example.html?source_system=oa&function_id=e2eIncoming&business_id=auto-sync-001
```

2. 登录或通过示例页获取 token。
3. 打开助手。
4. 选择真实附件 `incoming-risk-001.pdf`。
5. 首次查询应返回 `pending_sync`，前端随后自动调用 `/api/incoming-documents/ingest`。
6. 立即提问：

```text
请总结这个附件的主要风险。
```

通过标准：

- 如果任务还在下载、解析或摘要中，回答应明确提示附件正在同步或解析，不能编造附件内容。
- Web「来文管理」出现该附件，状态从 `uploaded/parsing/summarizing` 流转到 `ready`。
- 浏览器 Network 可看到：
  - `POST /api/incoming-documents/extractions/query`
  - `POST /api/incoming-documents/ingest`
  - 解析完成后再次查询返回 `matched + ready`。

## 场景三：chat-iframe 使用摘要、全文和网页上下文回答

### 3.1 附件 ready 后摘要问答

前置：场景二的附件状态已经为 `ready`。

步骤：

1. 刷新 `example.html`。
2. 选择 `incoming-risk-001.pdf`。
3. 提问：

```text
这个附件主要讲了什么？请用三点概括。
```

通过标准：

- 回答引用摘要中的真实信息。
- 不需要读取全文即可回答。
- 后端 run 的 `meta.iframe_context.files[0]` 包含：
  - `incomingId`
  - `summary`
  - `categories`
  - `items`，来源于 `document_business_extraction_items`，例如 `risk_item/task_item`
  - `hasMarkdown = true`
- 系统提示词注入摘要和业务结构化抽取 items，同时提供全文读取方式；摘要和结构化信息不足以回答细节问题时，再使用 `read_file` 读取全文。

### 3.2 追问细节触发 read_file

步骤：

1. 继续在同一个会话追问：

```text
附件里对整改截止时间、责任部门、具体措施是怎么写的？请按原文细节回答。
```

通过标准：

- 如果摘要不足，模型应调用 `read_file`。
- 工具调用面板或 run 事件中出现 `read_file`。
- 读取路径应来自 iframe 上下文提示，例如：

```text
全文读取：请使用 read_file 读取 /.../uploads/iframe-context/incoming/inc_xxx.md
```

- 回答应包含附件原文中的细节，不应只重复摘要。

失败定位：

- 没有 `read_file`：检查 `iframe_context_service.py` 是否给该附件生成 `全文读取` 行。
- 没有 Markdown 路径：检查来文详情中 `markdownFileUrl` 是否存在。
- 读不到文件：检查当前 thread sandbox 下是否生成 `iframe-context/incoming/<incomingId>.md`。

### 3.3 已存入知识库后的全文读取

步骤：

1. 在 Web「来文管理」选择该来文。
2. 点击「存入知识库」，选择目标知识库并提交。
3. 等待入库任务完成。
4. 回到 `chat-iframe`，刷新附件摘要。
5. 追问：

```text
请结合附件全文，列出所有涉及的部门和动作。
```

通过标准：

- 来文 `knowledgeImportStatus = indexed`。
- `linkedKbId`、`linkedFileId` 有值。
- iframe 上下文提供：

```text
全文读取：open_kb_document(kb_id="...", file_id="...")
```

- 模型优先使用知识库文档读取入口，不再依赖未入库来文临时 Markdown。

### 3.4 网页上下文 read_file

准备一个超过 8000 字的页面正文。可在 `example.html` 的正文区域插入长文本，或通过父脚本调用：

```js
chat.setPageContent({
  title: '长网页测试',
  url: location.href,
  text: '超过 8000 字的网页正文...'
})
```

步骤：

1. 打开助手。
2. 开启「问网页」。
3. 提问：

```text
请根据当前网页内容，找出第 6 节的验收要求。
```

通过标准：

- 对短网页，系统提示直接内联页面内容。
- 对长网页，系统提示只给预览，并提示：

```text
完整网页内容请使用 read_file 读取：/.../uploads/iframe-context/page.md
```

- 追问长网页细节时，模型调用 `read_file` 读取 `page.md`。

## 场景四：不同业务 ID 的聊天列表隔离

### 4.1 隔离规则

chat-iframe 父页面脚本生成：

```text
conversationScopeKey = source_system:function_id:business_id
```

创建会话时写入：

```json
{
  "metadata": {
    "source": "chat-iframe",
    "conversation_scope_key": "oa:e2eIncoming:case-001"
  }
}
```

拉取会话列表时请求：

```text
GET /api/chat/threads?limit=50&offset=0&agent_id=<agentId>&conversation_scope_key=<scope>
```

### 4.2 测试步骤

1. 打开业务 A：

```text
https://你的域名/chat-iframe/example.html?source_system=oa&function_id=e2eIncoming&business_id=case-001
```

2. 创建一条会话，发送：

```text
这是业务 A 的测试会话。
```

3. 打开业务 B：

```text
https://你的域名/chat-iframe/example.html?source_system=oa&function_id=e2eIncoming&business_id=case-002
```

4. 创建一条会话，发送：

```text
这是业务 B 的测试会话。
```

5. 分别刷新 A 和 B 页面。

通过标准：

- A 的会话列表只显示业务 A 的会话。
- B 的会话列表只显示业务 B 的会话。
- A/B 使用相同用户、相同 agent 时仍隔离。
- 如果 `business_id` 相同但 `function_id` 不同，也应隔离。

### 4.3 接口级验证

```bash
curl "$DOCMIND_API/chat/threads?limit=50&offset=0&conversation_scope_key=oa:e2eIncoming:case-001" \
  -H "Authorization: $DOCMIND_TOKEN"

curl "$DOCMIND_API/chat/threads?limit=50&offset=0&conversation_scope_key=oa:e2eIncoming:case-002" \
  -H "Authorization: $DOCMIND_TOKEN"
```

通过标准：

- 两次返回的 thread ID 不交叉。
- 每个 thread 的 `metadata.conversation_scope_key` 与查询 scope 一致。

未预上传时，iframe 会先按 `sourceUrl` 下载附件内容，再用 multipart 方式提交 `/api/incoming-documents/ingest`。因此附件下载地址必须是浏览器可直接访问的同源地址，或外部系统已正确开放 CORS；否则浏览器会在上传前拦截下载。浏览器 Network 中应能看到一次附件下载请求，以及一次 `Content-Type: multipart/form-data` 的来文上传请求。

## 回归检查矩阵

| 编号 | 场景 | 操作 | 通过标准 |
| --- | --- | --- | --- |
| E2E-01 | 外部 multipart 多文件上传 | 上传主文件和附件真实文件 | 返回 `accepted`，每个文件生成独立 `items` |
| E2E-02 | 原文与 Markdown 存储 | 查看来文详情和 MinIO 对象 | 原文和 Markdown 存在，数据库只存地址和元数据 |
| E2E-03 | 幂等上传 | 同一 `source_system + source_doc_id + source_file_id` 重复上传 | hash 相同返回 `exists` 或复用记录，不重复解析 |
| E2E-04 | iframe 预上传匹配 | 外部先以 `sourceSystem=oa` 上传，iframe 附件也带 `sourceSystem=oa` | 查询返回 `matched`，不重复自动上传 |
| E2E-05 | iframe 自动同步 | example 真实附件未预上传 | 首次 `pending_sync`，随后自动 ingest |
| E2E-06 | 解析中问答 | 任务未 ready 时提问 | 回答提示等待解析，不编造 |
| E2E-07 | ready 摘要问答 | 附件 ready 后提问概述 | 回答使用摘要和结构化信息 |
| E2E-08 | 附件细节追问 | 问摘要不足的细节 | 触发 `read_file` 或 `open_kb_document` |
| E2E-09 | 网页短上下文 | 页面正文短于 8000 字 | 直接按网页内容回答 |
| E2E-10 | 网页长上下文 | 页面正文长于 8000 字 | 追问细节触发 `read_file page.md` |
| E2E-11 | 会话隔离 | 切换不同 `business_id` | 会话列表不串 |
| E2E-12 | 来文入知识库 | 管理页点击存入知识库 | 入库完成后回写 `linkedKbId/linkedFileId` |
| E2E-13 | 知识库普通上传 | 直接在知识库上传文件 | 不触发业务结构化抽取 |

## 数据库与任务观察点

PostgreSQL 可检查：

```sql
select incoming_id, source_system, source_document_id, source_file_id, source_key,
       document_number, title, incoming_type, source_unit, incoming_date,
       is_main_file, filename,
       status, classification, knowledge_import_status, linked_kb_id, linked_file_id
from incoming_documents
order by created_at desc
limit 20;

select run_id, document_scope, incoming_id, kb_id, file_id, status, model_spec
from document_business_extraction_runs
order by created_at desc
limit 20;

select item_type, status, incoming_id, kb_id, file_id, left(source_quote, 80)
from document_business_extraction_items
order by created_at desc
limit 20;
```

Tasker 可观察：

- `incoming_document_process`：原文解析、摘要、业务抽取任务。
- `knowledge_ingest`：人工存入知识库后的知识库解析/索引任务。

## 常见失败定位

| 现象 | 优先检查 |
| --- | --- |
| multipart 上传 400 | 是否缺 `source_doc_id/files/file_metas`，`file_metas` 数量是否与文件一致，文件是否超过 100 MB |
| 自动同步后仍 not_found | Network 是否完成附件下载和 multipart `/incoming-documents/ingest`，`sourceKey/sourceSystem` 是否与查询一致 |
| 状态一直 parsing | worker 是否运行，Parser 是否支持该文件类型，MinIO 是否可读 |
| 状态 ready 但无摘要 | 模型配置是否可用，查看 `processing_error` |
| 业务抽取无结果 | 文档是否命中分类；业务抽取失败不会阻断来文 ready，需看后端 warning |
| 追问细节不读文件 | iframe 上下文是否包含 `全文读取` 行，模型是否启用了 `read_file` 工具 |
| 网页长文本没生成 page.md | `page.text/html` 是否真正超过 `IFRAME_PAGE_INLINE_CHARS = 8000` |
| 会话列表串业务 | URL 参数 `source_system/function_id/business_id` 是否缺失或相同 |

## 上线前结论标准

满足以下条件才认为来文与 chat-iframe E2E 可上线：

- E2E-01 到 E2E-13 全部通过。
- 至少 1 个真实 PDF 和 1 个真实 DOCX 完成 `ready`。
- 至少 1 次解析中问答明确提示等待，不编造内容。
- 至少 1 次附件细节追问触发 `read_file`。
- 至少 1 次长网页追问触发 `read_file`。
- 至少 2 个不同 `business_id` 的会话列表互不串扰。
- Web「来文管理」能查看原文、Markdown 预览、摘要，并能人工存入知识库。
