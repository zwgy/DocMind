# chat-iframe 上下文入库与读取实施方案

> **For agentic workers:** 后续实现请逐项勾选。目标是最小改动打通 iframe 页面/附件上下文，不新增独立上下文读取体系。

**目标：** chat-iframe 嵌入外部页面时，AI 能基于当前网页和选中附件回答问题，同时保持用户问题、历史消息干净，避免每轮把全文塞进 query。

**架构：** 前端只传结构化 `iframe_context`，后端在每次运行时把它渲染进 runtime system prompt。附件优先走 incoming documents 知识库链路，已入库文件用 `open_kb_document` 按需读取；长网页落到当前线程沙箱文件，给 `read_file` 路径。

**技术栈：** Vue 3 + Pinia + FastAPI + LangGraph + MinIO + existing Parser/open_kb_document/read_file。

---

## 结论与取舍

1. `/incoming-documents/ingest` 不新增接口，权限从 `get_admin_user` 改为 `get_required_user`。
2. 既然后端可以访问 `sourceUrl`，默认由后端下载附件，iframe 不下载文件。
3. 不再把“页面上下文/文件上下文”拼进 `query`；`query` 只保留用户原问题。
4. `iframe_context` 每轮随 run 传入，但不作为聊天历史消息保存；它是运行时上下文。
5. 有摘要时放摘要和 `kbId/fileId`；无摘要但已解析时给 `kbId/fileId`；未入库或未解析时标明状态，不让模型猜。
6. 网页优先用父页面传入的 `text`；没有 `text` 时后端把 `html` 转 markdown。短网页放 system，长网页写线程文件并给 `read_file` 路径。

## 当前代码事实

- `chat-iframe/src/apis/chat.ts` 的 `buildChatQuery()` 当前把页面/附件上下文拼到用户问题里。
- `chat-iframe/src/App.vue` 当前只取 `selectedPageFiles?.[0]`，多选附件会丢失。
- `/api/agent/runs` 在 `backend/package/yuxi/services/agent_run_service.py` 创建 run，只把少量 `meta` 写入 `input_payload`。
- `backend/package/yuxi/services/run_worker.py` 从 `input_payload` 重建 `meta`，未写入 `input_payload` 的字段到 worker 阶段会丢失。
- `backend/package/yuxi/services/chat_service.py` 调用 `build_agent_input_context()` 构造运行时 system prompt。
- `backend/server/routers/incoming_document_router.py` 的 `/incoming-documents/ingest` 当前使用 `get_admin_user`。
- `backend/package/yuxi/services/incoming_document_ingest_service.py` 的 `ingest_source_url()` 复用 `fetch_url_content()` 下载 URL。
- `backend/package/yuxi/knowledge/utils/url_fetcher.py` 当前只允许 `text/html` 和 `application/xhtml+xml`，直接下载 docx/pdf/xlsx 附件会失败，需要扩展。
- `backend/package/yuxi/agents/toolkits/kbs/tools.py` 已有 `open_kb_document`，适合作为 KB 文件全文读取工具。
- `backend/package/yuxi/agents/middlewares/attachment.py` 已把 `uploads` 注入 system prompt，适合普通聊天附件；网页上下文不建议混进用户附件列表。

## 目标数据结构

前端发送到 `/api/agent/runs`：

```json
{
  "query": "这份合同有哪些风险？",
  "meta": {
    "request_id": "uuid",
    "source": "chat-iframe",
    "iframe_context": {
      "page": {
        "title": "合同审批页",
        "url": "https://oa.example.com/form/123",
        "text": "页面可见文本",
        "html": "<section>...</section>"
      },
      "files": [
        {
          "id": "att-123",
          "name": "合同.docx",
          "sourceUrl": "https://oa.example.com/download?id=123",
          "sourceKey": "123",
          "matchStatus": "matched",
          "extractionStatus": "ready",
          "fileStatus": "parsed",
          "hasParsedMarkdown": true,
          "kbId": "kb_xxx",
          "fileId": "file_xxx",
          "summary": "合同主体、金额、付款条款...",
          "summaryTruncated": false
        }
      ]
    }
  }
}
```

后端渲染到 system prompt 的形态：

```text
### iframe 页面与附件上下文
用户问题可能与当前嵌入页和选中附件有关。优先依据下列摘要回答；摘要不足时按给定工具路径读取全文。不要编造尚未解析完成的附件内容。

【当前网页】
标题：合同审批页
地址：https://oa.example.com/form/123
内容预览：
...
[已截断，完整网页内容请使用 read_file 读取：/home/gem/user-data/uploads/iframe-context/page.md]

【选中附件】
- 合同.docx
  状态：已有摘要
  摘要：...
  全文读取：open_kb_document(kb_id="kb_xxx", file_id="file_xxx")
```

## 任务 1：放开 incoming ingest 权限并修正文档 URL 下载

**文件：**

- 修改：`backend/server/routers/incoming_document_router.py`
- 修改：`backend/package/yuxi/knowledge/utils/url_fetcher.py`
- 修改：`backend/package/yuxi/services/incoming_document_ingest_service.py`
- 测试：`backend/test/services/test_incoming_document_ingest_service.py`

**实施：**

- [ ] 把 `/incoming-documents/ingest` 的依赖从 `get_admin_user` 改为 `get_required_user`。
- [ ] 保留 `operator_id=current_user.uid`，保证入库记录仍能追踪操作者。
- [ ] 给 `fetch_url_content()` 增加可选参数 `allowed_content_types`，默认仍是 HTML，避免影响网页解析调用方。
- [ ] 在 `IncomingDocumentIngestService.ingest_source_url()` 调用下载时传入文档 MIME 白名单，并把 `max_size` 设为 `MAX_UPLOAD_SIZE_BYTES`。
- [ ] 文档白名单至少覆盖 docx/pdf/xls/xlsx/pptx/txt/md/csv/json/html：

```python
INCOMING_ALLOWED_CONTENT_TYPES = (
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "text/html",
    "application/xhtml+xml",
    "application/octet-stream",
)
```

**验收：**

- 登录用户可调用 JSON 形式 `/api/incoming-documents/ingest`。
- 非登录请求仍返回 401。
- docx/pdf 类型 `sourceUrl` 可被后端下载并进入入库任务。
- `fetch_url_content()` 默认行为仍只允许 HTML。

**测试命令：**

```bash
uv run pytest backend/test/services/test_incoming_document_ingest_service.py
```

## 任务 2：增强 incoming extraction 查询状态

**文件：**

- 修改：`backend/package/yuxi/services/incoming_document_service.py`
- 修改：`chat-iframe/src/types.ts`
- 测试：`backend/test/services/test_incoming_document_service.py`

**实施：**

- [ ] `_query_one()` 匹配到 KB 文件时返回 `fileStatus` 和 `hasParsedMarkdown`。
- [ ] `_extraction_payload()` 区分三类状态：
  - `extractionStatus="ready"`：有业务摘要。
  - `extractionStatus="running"`：摘要任务运行中。
  - `extractionStatus="not_found"`：没有摘要，但如果 `hasParsedMarkdown=true`，LLM 仍可用 `open_kb_document` 读全文。
- [ ] 对 `pending_sync` 返回 `sourceUrl/sourceKey/name`，前端可据此触发 ingest。
- [ ] 前端 `ExtractionResult` 增加：

```ts
fileStatus?: string
hasParsedMarkdown?: boolean
taskId?: string | null
```

**验收：**

- 已解析但无摘要的文件返回 `matchStatus="matched"`、`hasParsedMarkdown=true`、`kbId/fileId`。
- 未匹配但有 `sourceUrl/sourceKey` 的文件返回 `matchStatus="pending_sync"`。
- 前端类型能表达上述状态。

**测试命令：**

```bash
uv run pytest backend/test/services/test_incoming_document_service.py
```

## 任务 3：前端构造 iframe_context，停止污染 query

**文件：**

- 修改：`chat-iframe/src/apis/chat.ts`
- 修改：`chat-iframe/src/App.vue`
- 修改：`chat-iframe/src/stores/chat.ts`
- 修改：`chat-iframe/src/types.ts`
- 测试：`chat-iframe/test/parent-script.test.js`

**实施：**

- [ ] `buildChatQuery(input)` 改为只返回 `input.text.trim()`。
- [ ] 新增 `buildIframeContext()`，输入 `pageContent`、全部 `selectedPageFiles`、对应 extraction results，输出 `meta.iframe_context`。
- [ ] `App.vue` 不再只取第一个附件，传全部 `selectedPageFiles` 和 `results`。
- [ ] `sendMessageStream()` 的 `meta` 中新增 `iframe_context`，保留旧的 `page_content/selected_file/extraction_result` 到可删前的过渡期也可以，但后端只使用 `iframe_context`。
- [ ] `chat.send()` 传入 `selectedPageFiles` 和 `extractionResults`。

**验收：**

- 用户消息历史中只看到原问题。
- 选择多个附件时，`iframe_context.files` 包含全部选中附件。
- 未选择“问网页”时不发送 page。
- 未选择“问文件”时不发送 files。

**测试命令：**

```bash
cd chat-iframe
pnpm test
```

## 任务 4：后端保存并传递 iframe_context

**文件：**

- 修改：`backend/package/yuxi/services/agent_run_service.py`
- 修改：`backend/package/yuxi/services/run_worker.py`
- 测试：`backend/test/services/test_agent_run_service.py`

**实施：**

- [ ] `create_agent_run()` 从 `meta` 中读取 `iframe_context`，写入 `input_payload["iframe_context"]`。
- [ ] 输入消息 `extra_metadata` 可只保存轻量标记，例如：

```python
if (meta or {}).get("iframe_context"):
    input_metadata["iframe_context"] = {"enabled": True}
```

不要把网页全文或附件摘要塞进 message metadata。

- [ ] `process_agent_run()` 从 `payload["iframe_context"]` 恢复到 `meta["iframe_context"]`。
- [ ] run metadata event 不需要发送完整 `iframe_context`，避免 SSE 泄露和变大。

**验收：**

- worker 阶段能拿到 `meta["iframe_context"]`。
- 数据库 `messages.content` 仍是原始用户问题。
- run `input_payload` 包含结构化上下文，便于重放和排查。

**测试命令：**

```bash
uv run pytest backend/test/services/test_agent_run_service.py
```

## 任务 5：后端归一化网页上下文并落长网页文件

**文件：**

- 新增：`backend/package/yuxi/services/iframe_context_service.py`
- 修改：`backend/package/yuxi/services/chat_service.py`
- 测试：`backend/test/unit/services/test_iframe_context_service.py`

**实施：**

- [x] 新建 `render_iframe_context_prompt(thread_id, uid, iframe_context)`。
- [x] 页面处理规则：
  - 优先用 `page.text`。
  - 没有 `text` 且有 `html` 时，把 HTML 写入临时 `.html`，调用 `Parser.aparse()` 转 markdown。
  - 页面区段能够完整放入实际剩余预算时直接内联。
  - 无法完整放入时写入 `{save_dir}/threads/{thread_id}/user-data/uploads/iframe-context/page.md`。
  - 对模型暴露路径 `/home/gem/user-data/uploads/iframe-context/page.md`。
- [x] system prompt 使用单一总预算：

```python
IFRAME_CONTEXT_TOTAL_CHARS = 4000
```

- [x] 附件摘要、结构化结果和页面使用分区配额；先保留附件定位及结构化信息，页面预览自动使用剩余空间，不再对最终完整提示词做尾部截断。
- [x] 区段内发生截断时追加：

```text
[已截断，更多内容请使用给定工具读取]
```

- [x] `chat_service.stream_agent_chat()` 和 `agent_chat()` 在 `_apply_model_override()` 后调用 renderer，把返回文本追加到 `input_context["system_prompt"]`。

**验收：**

- 能够放入页面区段预算的网页进入 system prompt。
- 无法放入页面区段预算的网页落盘，system prompt 只包含预览和 `read_file` 路径。
- 页面、附件摘要和结构化结果同时存在时，三类信息均保留且总长度不超过 4000 字符。
- 没有 page/files 时不改变 system prompt。
- renderer 不写普通聊天附件状态，不污染附件列表。

**测试命令：**

```bash
uv run pytest backend/test/services/test_iframe_context_service.py
```

## 任务 6：渲染附件上下文和读取指针

**文件：**

- 修改：`backend/package/yuxi/services/iframe_context_service.py`
- 测试：`backend/test/unit/services/test_iframe_context_service.py`

**实施：**

- [x] 对每个 `iframe_context.files` 渲染明确状态。
- [x] `summary_ready`：渲染摘要和已验证的知识库定位参数。
- [x] `matched + hasParsedMarkdown=true`：无摘要时显示已解析状态，并保留可用的定位参数。
- [x] `pending_sync/ingesting/parsing`：提示附件未就绪，不给读取路径，不允许模型猜。
- [x] `multiple`：提示匹配到多个候选，需要用户或系统明确文件。
- [x] 所有附件摘要公平共享附件区段预算，结构化结果使用独立区段；最终上下文只受 `IFRAME_CONTEXT_TOTAL_CHARS` 单一总预算控制。

**状态文案示例：**

```text
- 合同.docx
  状态：已解析，暂无结构化摘要
  全文读取：open_kb_document(kb_id="kb_xxx", file_id="file_xxx")
```

```text
- 合同.docx
  状态：正在入库或解析，当前不能读取全文。不要猜测该附件内容；如果问题依赖它，请说明需要等待解析完成。
```

**验收：**

- 有摘要时模型先看到摘要。
- 摘要不足时模型知道用 `open_kb_document`。
- 未就绪附件不会出现虚假的读取路径。

**测试命令：**

```bash
uv run pytest backend/test/services/test_iframe_context_service.py
```

## 任务 7：前端触发未匹配附件入库并轮询状态

**文件：**

- 修改：`chat-iframe/src/apis/incoming-documents.ts`
- 修改：`chat-iframe/src/App.vue`
- 修改：`chat-iframe/src/types.ts`
- 测试：`chat-iframe/test/parent-script.test.js`

**实施：**

- [ ] 新增前端 API 方法：

```ts
export async function ingestIncomingDocument(file: IncomingPageFile, token?: string) {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`
  const response = await fetch(apiUrl('/api/incoming-documents/ingest'), {
    method: 'POST',
    headers,
    body: JSON.stringify({
      sourceUrl: file.sourceUrl || file.url,
      sourceKey: file.sourceKey || file.id,
      filename: file.name
    })
  })
  if (!response.ok) {
    let message = `入库失败：${response.status}`
    try {
      const data = await response.json()
      message = data.detail || data.message || message
    } catch {
      // 后端非 JSON 错误保持 HTTP 状态，便于定位接入问题。
    }
    throw new Error(message)
  }
  return response.json()
}
```

- [ ] `refreshExtraction()` 遇到 `pending_sync` 且有 `sourceUrl/sourceKey` 时调用 ingest。
- [ ] ingest 返回 `accepted/exists` 后继续用 `/extractions/query` 刷新。
- [ ] 轮询策略保持简单：发送前刷新一次；如果仍未就绪，不阻塞聊天。
- [ ] 不做父页面下载 fallback。

**验收：**

- 未入库附件会自动触发 `/incoming-documents/ingest`。
- 后端返回 `accepted` 后，用户可以立即提问；模型会看到“正在入库/解析”状态。
- 后续刷新到 `kbId/fileId` 后，模型可以使用 `open_kb_document`。

**测试命令：**

```bash
cd chat-iframe
pnpm test
```

## 任务 8：补充文档和回归检查

**文件：**

- 修改：`chat-iframe/README.md`
- 修改：`docs/develop-guides/changelog.md`

**实施：**

- [ ] README 说明 `query` 不再携带网页/附件正文，改由 `meta.iframe_context` 传递。
- [ ] README 说明父页面文件字段至少需要 `name/sourceUrl/sourceKey`。
- [ ] changelog 记录 chat-iframe 上下文读取策略变更。

**总体验收：**

- 问网页：短网页可直接回答，长网页会触发 `read_file`。
- 问文件：有摘要先用摘要；摘要不够用 `open_kb_document`。
- 多附件：全部选中附件都进入上下文。
- 用户历史：只保存用户原问题，不再保存“页面上下文/文件上下文”拼接文本。
- 未解析附件：模型明确说明等待解析，不编造。

**建议回归命令：**

```bash
uv run pytest backend/test/services/test_incoming_document_service.py backend/test/services/test_incoming_document_ingest_service.py backend/test/services/test_iframe_context_service.py
cd chat-iframe
pnpm test
```

## 不做的事

- 不新增 `/incoming-documents/ensure`，直接复用 `/incoming-documents/ingest`。
- 不实现 iframe/父页面下载文件 fallback，因为当前确认后端可访问 `sourceUrl`。
- 不新增网页解析依赖，先复用现有 `Parser.aparse()` 的 HTML -> markdown 能力。
- 不把 iframe 网页写入知识库。网页是当前线程临时上下文，落线程文件即可。
- 不把完整 `iframe_context` 保存进聊天 message metadata，避免历史膨胀。
