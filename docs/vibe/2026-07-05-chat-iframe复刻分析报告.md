# chat-iframe 与 web 前端聊天功能复刻分析报告

日期：2026-07-05  
作者：Claude（自动分析）  
关联项目：

- 主项目聊天实现：`web/src/components/{AgentChatComponent,AgentInputArea,AgentMessageComponent,AgentPanel,...}.vue`
- 嵌入实现：`chat-iframe/src/**` + `chat-iframe/public/docmind-chat-iframe-parent.js`
- 关联方案：[2026-07-02-AI文档智能助手第一版开发方案.md](./2026-07-02-AI文档智能助手第一版开发方案.md)
- 关联计划：[2026-07-02-AI文档智能助手第一版实施计划.md](./2026-07-02-AI文档智能助手第一版实施计划.md)

---

## 1. 目的

为后续迭代提供基线，回答三个问题：

1. chat-iframe 第一版复刻了 web 前端聊天能力的百分之多少？
2. 还有哪些功能没有复刻，需要开发？
3. 哪些能力是 chat-iframe 独有（web 主站没有的）？

输出三份结构化清单：**web 前端功能总览**、**chat-iframe 已实现**、**chat-iframe 待开发**。

---

## 2. 现状概述

### 2.0 2026-07-05 实施更新

本报告初版分析后，`chat-iframe` 已按“核心聊天优先”补齐一轮 web 聊天能力轻量复刻：

- 已完成：Markdown 表格/代码/KaTeX 渲染、推理过程折叠、结构化 SSE 解析、工具调用摘要、停止生成、重试、复制、点赞/点踩反馈、图片上传、附件预览、会话重命名/删除/置顶。
- 仍暂缓：完整 @ 提及、状态面板、AgentPanel 文件工作区、HumanApproval、SubagentThread、Artifact 面板、主站 Ant Design 弹窗体系。
- 取舍原因：`chat-iframe` 需要独立 Docker 构建并无感嵌入生产系统，本轮不直接依赖 `web/src`，也不引入 Ant Design Vue。

### 2.1 定位差异

`chat-iframe/README.md:20` 明确写了第一版的取舍：

> 第一版已接入聊天会话、流式回答、模型选择、输入框附件、问网页和问文件开关。**复杂能力如中断恢复、子智能体详情、artifact 面板和聊天记录富渲染仍复用主站后续能力，不在 iframe 第一版里重复实现。**

因此 chat-iframe **不是要把 web 主站全量复刻**，而是"**来文场景的轻量嵌入**"——来文抽取结果展示是核心场景独有，主体功能刻意做了减法。

### 2.2 总体复刻度

| 维度 | 已复刻 | 待开发 | 复刻率 |
|------|--------|--------|--------|
| 聊天核心体验 | 会话 CRUD + 流式 + 模型选择 + 附件 + Markdown/代码/公式 + 推理过程 + 停止/重试/复制 + 图片 + 轻量工具/反馈 | 完整 @ 提及 / 主站级引用详情 / 流式续连 / 自动标题 | **约 65%** |
| 状态/调试 | 无 | 全部 6 个状态面板区块 | 0% |
| 智能体/任务 | 无 | HumanApproval / SubagentThread / Artifacts | 0% |
| iframe 特有能力 | 全部已实现 | 无 | 100% |
| 来文特有能力 | 全部已实现 | 无 | 100% |

### 2.3 技术栈对比

| 维度 | web 前端 | chat-iframe |
|------|---------|-------------|
| 语言 | JavaScript + Vue 3 | TypeScript + Vue 3 |
| 状态管理 | Pinia | Pinia |
| UI 库 | Ant Design Vue（部分） | 原生 CSS（base.css/app.css） |
| Markdown | MarkdownPreview（自研） | 已接入轻量 `MarkdownPreview` |
| 代码高亮 | 自研组件 | 已接入 highlight.js core 常用语言 |
| 流式实现 | EventSource / fetch SSE（Smoothed） | fetch + ReadableStream |
| 路由 | Vue Router（HomeView / WorkspaceView） | 无（单页应用） |
| 构建 | Vite | Vite |
| 测试 | Vitest | node:test（仅父脚本） |

---

## 3. web 前端聊天功能清单

### 3.1 会话（Thread）

| # | 功能 | 实现位置 |
|---|------|---------|
| 1.1 | 新建会话（chatThreadsStore.createThread） | `web/src/stores/chatThreads.js` |
| 1.2 | 切换会话（selectChat） | `web/src/components/AgentChatComponent.vue:2191-2252` |
| 1.3 | 删除/重命名/置顶会话 | chatThreadsStore（CRUD 接口） |
| 1.4 | 多智能体下会话按 agent 分组 | `singleMode` prop 切换 |
| 1.5 | 会话列表加载（loadThreads / loadChatsList） | `AgentChatComponent.vue:2664-2695` |
| 1.6 | 自动滚动到底部（ScrollController） | `web/src/utils/scrollController.js` |
| 1.7 | 自动生成会话标题（agentApi.generateTitle） | `AgentChatComponent.vue:2341-2358`（fast_model） |
| 1.8 | 跨会话还原模型选择 | `AgentChatComponent.vue:1998-2009` |
| 1.9 | 运行中切换配置的提示条（CONFIG_CHANGE_NOTICE） | `AgentChatComponent.vue:1710-1831` |
| 1.10 | 启动屏幕随机问候语 | `AgentChatComponent.vue:636-645` |

### 3.2 输入区

| # | 功能 | 实现位置 |
|---|------|---------|
| 2.1 | 多行文本（textarea + Enter 发送 / Shift+Enter 换行） | `web/src/components/MessageInputComponent.vue` |
| 2.2 | 图片粘贴 / 图片选择（currentImage / ImagePreviewComponent） | `web/src/components/AgentInputArea.vue:102-130` |
| 2.3 | 附件上传（AttachmentOptionsComponent → uploadAttachment） | `web/src/components/AttachmentOptionsComponent.vue` |
| 2.4 | 临时附件弹窗（AttachmentTmpUploadModal） | `web/src/components/AttachmentTmpUploadModal.vue` |
| 2.5 | 附件预览卡 + 移除按钮 | `AgentInputArea.vue:24-46` |
| 2.6 | @ 提及（useAgentMentionConfig + MentionTextRenderer） | `web/src/composables/useAgentMentionConfig.ts` + `web/src/components/common/MentionTextRenderer.vue` |
| 2.7 | 智能体能力感知（capabilities.includes('file_upload')） | `AgentChatComponent.vue:984-994` |
| 2.8 | 发送冷却（startSendCooldown 2s） | `AgentChatComponent.vue:1639-1648` |
| 2.9 | 占位文案"问点什么？使用 @ 可以提及哦~" | `AgentInputArea.vue:104` |
| 2.10 | 加载/禁用状态反馈 | `is-loading` / `:disabled` |
| 2.11 | 草稿态模型选择迁移到新建线程 | `AgentChatComponent.vue:2308-2317` |
| 2.12 | `defineExpose: focus / closeOptions` | `AgentInputArea.vue:154-157` |

### 3.3 模型与智能体

| # | 功能 | 实现位置 |
|---|------|---------|
| 3.1 | ModelSelectorComponent 按线程记忆选择 | `web/src/components/ModelSelectorComponent.vue` |
| 3.2 | 单/多智能体模式（singleMode） | `AgentChatComponent.vue:935-940` |
| 3.3 | 切换智能体时清空全部线程状态 | `AgentChatComponent.vue:2747-2765` |
| 3.4 | 智能体默认模型回退链 | `AgentChatComponent.vue:957-963` |
| 3.5 | 模型 spec 写入消息 meta 与发送请求 | `AgentChatComponent.vue:2385` |
| 3.6 | 自动选择首个非置顶会话 | `getFirstNonPinnedChat:2186-2189` |

### 3.4 消息渲染（AgentMessageComponent）

| # | 功能 | 实现位置 |
|---|------|---------|
| 4.1 | 三种角色：human / ai / system（独立样式 + 边框/背景/对齐） | `AgentMessageComponent.vue:14-37` |
| 4.2 | Markdown 渲染（MarkdownPreview） | `web/src/components/common/MarkdownPreview.vue` |
| 4.3 | 代码高亮 | MarkdownPreview 内部 |
| 4.4 | 数学公式 / Mermaid 图表 | MarkdownPreview 内部 |
| 4.5 | **reasoning_content** 折叠面板（"推理过程"/"正在思考…"） | `AgentMessageComponent.vue:40-53` |
| 4.6 | **ToolCallsGroupComponent** 工具调用可视化 | `web/src/components/ToolCallsGroupComponent.vue` |
| 4.7 | **RefsComponent** 知识库引用 / 网络来源 / 复制 / 模型切换 | `web/src/components/RefsComponent.vue` |
| 4.8 | 多模态图片（image_content + 全屏预览 + ESC 关闭） | `AgentMessageComponent.vue:2-13, 134-145, 208-230` |
| 4.9 | 附件卡片列表（normalizeAttachmentPreviews） | `AgentMessageComponent.vue:113-132, 301-303` |
| 4.10 | 错误提示（interrupted / unexpect / content_guard_blocked / agent_error） | `AgentMessageComponent.vue:266-295` |
| 4.11 | 用户消息复制按钮 + 复制完成反馈（Check 图标 2s） | `AgentMessageComponent.vue:23-31, 232-260` |
| 4.12 | 重试链接（retryStoppedMessage） | `AgentMessageComponent.vue:81-86` |
| 4.13 | 调试模式状态信息（infoStore.debugMode） | `AgentMessageComponent.vue:107, 507-518` |
| 4.14 | @ 提及渲染（标签化显示） | `AgentMessageComponent.vue:32-34, 308` |
| 4.15 | 富文本 fallback（white-space: pre-line） | `AgentMessageComponent.vue:367-371` |

### 3.5 流式与生命周期

| # | 功能 | 实现位置 |
|---|------|---------|
| 5.1 | useAgentRunStream（建立 SSE 订阅） | `web/src/composables/useAgentRunStream.ts` |
| 5.2 | useAgentStreamHandler（解析 chunk） | `web/src/composables/useAgentStreamHandler.ts` |
| 5.3 | useStreamSmoother（流式平滑） | `web/src/composables/useStreamSmoother.ts` |
| 5.4 | 主动取消 run（agentApi.cancelAgentRun） | `AgentChatComponent.vue:2410-2422` |
| 5.5 | 用户停止后保留"重新编辑问题"入口 | `AgentMessageComponent.vue:81-86` |
| 5.6 | 恢复运行（resumeActiveRunForThread） | `AgentChatComponent.vue:2166-2177, 2453-2471` |
| 5.7 | 切回标签页自动续流（visibilitychange） | `AgentChatComponent.vue:1908-1921, 2179-2182` |
| 5.8 | 切换会话前 stopThreadStream + stopRunStreamSubscription | `AgentChatComponent.vue:2204-2209` |
| 5.9 | 乐观插入用户消息（避免空白等待） | `AgentChatComponent.vue:1657-1693` |
| 5.10 | 取消后回滚附件标记 | `rollbackAttachments:1705-1708` |
| 5.11 | 错误处理（handleChatError / handleValidationError） | `web/src/utils/errorHandler.js` |

### 3.6 侧边面板（AgentPanel）

| # | 功能 | 实现位置 |
|---|------|---------|
| 6.1 | 可拖拽调整宽度（panelRatio + rAF 防抖） | `web/src/components/AgentPanel.vue:544-602` |
| 6.2 | 文件树懒加载（loadData） | `AgentPanel.vue:383-392` |
| 6.3 | 多 Tab 文件预览（previewTabs + activePreviewPath） | `AgentPanel.vue:6-34, 880-900` |
| 6.4 | 文件下载（Content-Disposition 解析中文文件名） | `AgentPanel.vue:316-334, 513-533` |
| 6.5 | 文件删除（含删除目录 + 确认弹窗） | `AgentPanel.vue:482-511` |
| 6.6 | 工作区切换视图（tree / preview） | `AgentPanel.vue:472-474, 868-872` |
| 6.7 | 文件系统 viewer_filesystem 完整接入 | `web/src/apis/viewer_filesystem.js` |
| 6.8 | 加载/错误/空态三类视图 | `AgentPanel.vue:86-92` |
| 6.9 | AgentFilePreview 文件预览组件 | `web/src/components/AgentFilePreview.vue` |
| 6.10 | AgentArtifactsCard 产物卡片 | `web/src/components/AgentArtifactsCard.vue` |

### 3.7 状态面板（State Panel）

| # | 功能 | 实现位置 |
|---|------|---------|
| 7.1 | Token 用量可视化（segment 横向条 + 摘要/系统/工具/已压缩分段） | `AgentChatComponent.vue:1018-1100` |
| 7.2 | 当前上下文窗口（context_window / remaining） | `AgentChatComponent.vue:1108-1129, 1145-1157` |
| 7.3 | Todo 列表（completed / in_progress / pending / cancelled 图标） | `AgentChatComponent.vue:1179-1190, 320-340` |
| 7.4 | 附件/文件清单 | `AgentChatComponent.vue:1236-1261` |
| 7.5 | 产物清单（artifacts，可点击进入预览） | `AgentChatComponent.vue:1165-1178, 411-435` |
| 7.6 | 子智能体运行列表（合并 running + completed） | `AgentChatComponent.vue:1191-1215, 1438-1463` |
| 7.7 | 流式期间定时刷新（5s） | `AgentChatComponent.vue:1874-1880` |
| 7.8 | 自适应浮窗/停靠模式（docked / floating） | `AgentChatComponent.vue:780-786` |
| 7.9 | 折叠/展开 section | `AgentChatComponent.vue:1232-1235` |
| 7.10 | 状态摘要（stateSummaryLabel） | `AgentChatComponent.vue:1272-1288` |

### 3.8 智能体/任务相关

| # | 功能 | 实现位置 |
|---|------|---------|
| 8.1 | HumanApprovalModal（人工审批） | `web/src/components/HumanApprovalModal.vue` |
| 8.2 | SubagentThreadModal（子智能体详情弹窗） | `web/src/components/SubagentThreadModal.vue` |
| 8.3 | AgentArtifactsCard（产物卡片） | `web/src/components/AgentArtifactsCard.vue` |
| 8.4 | ToolCallingResult/toolRegistry（任务工具注册） | `web/src/components/ToolCallingResult/toolRegistry.js` |
| 8.5 | makeChildThreadId（前端推算子线程 ID） | `web/src/utils/subagentThread.js` |
| 8.6 | activeSubagentToolCallIds（活跃 task 判定） | `AgentChatComponent.vue:1405-1413` |
| 8.7 | FallbackAvatar 像素头像生成 | `web/src/components/common/FallbackAvatar.vue` + `web/src/utils/pixelAvatar.js` |

### 3.9 API（agent_api.js）

| # | 接口 | 用途 |
|---|------|------|
| 9.1 | createAgentRun（query/agent_id/thread_id/meta/image_content/model_spec/resume/parent_run_id） | 启动 run |
| 9.2 | cancelAgentRun | 取消 run |
| 9.3 | getAgentHistory | 加载会话历史 |
| 9.4 | getAgentState（agent_state: token_usage / todos / files / artifacts / subagent_runs） | 状态面板数据源 |
| 9.5 | generateTitle（fast_model） | 自动生成会话标题 |
| 9.6 | listThreadFiles | 工作区文件清单 |
| 9.7 | deleteThreadAttachment | 删除附件 |
| 9.8 | getThreadAttachments | 加载附件 |
| 9.9 | selectAgent | 切换智能体 |

### 3.10 通用 UI 行为

| # | 功能 | 实现位置 |
|---|------|---------|
| 10.1 | 路由进入选择线程（selectThreadFromRoute） | `AgentChatComponent.vue:2254-2285` |
| 10.2 | 配置变更检测（agentStore 切换 / agentConfig JSON 变更） | `AgentChatComponent.vue:2778-2789` |
| 10.3 | 单/多智能体模式（singleMode prop） | `AgentChatComponent.vue:935-940` |
| 10.4 | 国际化（i18n 占位） | 整个 web/src/locales |
| 10.5 | 暗色模式（CSS 变量） | `web/src/assets/css/base.css` |
| 10.6 | Page Visibility 续流 | `AgentChatComponent.vue:2179-2182` |
| 10.7 | ResizeObserver 自适应布局 | `AgentChatComponent.vue:1882-1906` |
| 10.8 | 移动端断点（mobilePanelBreakpoint = 768） | `AgentChatComponent.vue:717` |

### 3.11 导出与调试

| # | 功能 | 实现位置 |
|---|------|---------|
| 11.1 | buildExportPayload（导出 agent 名/描述/消息全量 JSON） | `AgentChatComponent.vue:2491-2510` |
| 11.2 | defineExpose: selectThreadFromRoute + getExportPayload | `AgentChatComponent.vue:2512-2515` |
| 11.3 | 调试模式（status-info 区） | `AgentMessageComponent.vue:107, 507-518` |

**合计：60+ 项功能**。

---

## 4. chat-iframe 已实现清单（11 项）

| # | 功能 | 实现位置 |
|---|------|---------|
| ① | 会话列表 + 新建 + 选择 | `chat-iframe/src/stores/chat.ts` bootstrap / newConversation / selectThread |
| ② | 消息历史加载 | `chat-iframe/src/apis/chat.ts:199` listMessages |
| ③ | 流式响应（SSE via fetch + ReadableStream） | `chat-iframe/src/apis/chat.ts:157-177` readRunEventStream |
| ④ | 模型下拉选择（API: listChatModels） | `chat-iframe/src/components/ChatInput.vue:72-82` + `chat-iframe/src/apis/models.ts` |
| ⑤ | 输入框附件上传（uploadAttachment + confirmThreadAttachments） | `chat-iframe/src/components/ChatInput.vue:83-86` + `chat-iframe/src/stores/chat.ts:100` attachFiles |
| ⑥ | 问网页 / 问文件开关（query 拼接） | `chat-iframe/src/components/ChatInput.vue:65-71` + `chat-iframe/src/apis/chat.ts:78-97` buildChatQuery |
| ⑦ | 助手消息工具事件提示（toolEvents 文本行） | `chat-iframe/src/components/ChatMessages.vue:30-32` |
| ⑧ | 来文抽取结果展示（matchStatus / extractionStatus） | `chat-iframe/src/components/IncomingDocumentPanel.vue` + `chat-iframe/src/apis/incoming-documents.ts` |
| ⑨ | 页面附件选择（多附件默认首项） | `chat-iframe/src/components/PageFileSelector.vue` + `chat-iframe/src/stores/iframe-context.ts:13-31` normalizeFiles |
| ⑩ | iframe ↔ 父页面 postMessage 桥（INIT_CONFIG / PAGE_CONTENT / FILE_LIST / WINDOW_STATE / IFRAME_READY / REQUEST_PAGE_CONTENT / REQUEST_FILE_LIST） | `chat-iframe/src/composables/useIframeBridge.ts` |
| ⑪ | 父页面 SDK（悬浮按钮 / 拖拽 / 最大化 / 最小化 / 关闭 / 事件订阅） | `chat-iframe/public/docmind-chat-iframe-parent.js` |

> 简单说：**会话 CRUD + 流式 + 模型选择 + 附件 + 来文场景的 4 个 iframe-only 能力**。

---

## 5. chat-iframe 待开发清单（按优先级）

### 🔴 P0 — 聊天核心体验（强烈建议下个迭代补齐）

实施更新：P0-1、P0-2、P0-3、P0-4、P0-5、P0-7、P0-8、P0-9、P0-10、P0-12 已完成轻量版；P0-6 已完成基础反馈/复制/模型展示但未完整复刻主站来源详情；P0-11 和 P0-13 暂缓。

| # | 缺失项 | web 端实现位置 | 备注 |
|---|--------|---------------|------|
| P0-1 | **Markdown 渲染** | `web/src/components/common/MarkdownPreview.vue` | chat-iframe 仅渲染纯文本 [ChatMessages.vue:28](chat-iframe/src/components/ChatMessages.vue)，代码块、列表、加粗、表格全部不可读 |
| P0-2 | **代码高亮** | MarkdownPreview 内部 | 来文场景里经常返回代码与表格 |
| P0-3 | **数学公式 / Mermaid 图表** | MarkdownPreview 内部 | 决策报告经常含流程图与公式 |
| P0-4 | **思考过程折叠（reasoning_content）** | `AgentMessageComponent.vue:40-53` | 不展示推理过程，用户看不到 AI 在做什么 |
| P0-5 | **工具调用可视化** | `ToolCallsGroupComponent.vue` | 当前仅显示文本行 "工具调用：xxx"，看不到完整过程 |
| P0-6 | **知识库 / 网络来源引用** | `RefsComponent.vue` | 来文场景依赖知识库，但没有引用与原文链接展示 |
| P0-7 | **停止/中断生成按钮** | `handleSendOrStop + cancelAgentRun` | 用户无法中断长回答 |
| P0-8 | **重试 / 重新生成** | `retryMessage` | 失败或回答不理想时无法重新提问 |
| P0-9 | **用户消息复制按钮** | `AgentMessageComponent.vue:23-31` | 复制回答需要手动选中 |
| P0-10 | **多模态图片消息** | `message.image_content` 渲染 | 用户发送图片 / 助手识别图片能力缺失 |
| P0-11 | **@ 提及（智能体/文件/工具）** | `MentionTextRenderer + useAgentMentionConfig` | 无法在输入时引用特定智能体或知识库 |
| P0-12 | **错误提示分类**（interrupted / guard_blocked / unexpect） | `AgentMessageComponent.vue` 错误块 | 当前仅显示 assistantMessage.content 文字，无法定位错误类型 |
| P0-13 | **流式平滑（useStreamSmoother）** | `web/src/composables/useStreamSmoother.ts` | 当前事件逐块直接拼接，渲染抖动明显 |

### 🟡 P1 — 状态 / 调试能力（按需补齐）

| # | 缺失项 | web 端实现位置 | 备注 |
|---|--------|---------------|------|
| P1-1 | Token 用量可视化 | `AgentChatComponent.vue:1018-1100` tokenUsageSegments | 与来文场景关联度低，但用户关心回答成本 |
| P1-2 | Todo / 任务进度展示 | `AgentChatComponent.vue:1179-1190` | 来文场景几乎用不到 task 工具调用 |
| P1-3 | 产物 / Artifacts 列表 | `state-panel + AgentArtifactsCard` | 与 file_panel 重复，迁移成本高 |
| P1-4 | 子智能体运行详情 | `state-panel + SubagentThreadModal` | 来文场景几乎不用 subagent |
| P1-5 | 附件 / 文件状态 | state-panel files | 与 IncomingDocumentPanel 部分重叠 |
| P1-6 | 文件树 / 文件预览面板 | `AgentPanel + AgentFilePreview + FileTreeComponent` | 需要后端 viewer_filesystem 接口配合 |
| P1-7 | 工作区产物下载 | viewer_filesystem downloadViewerFile | 来文场景几乎不用 |
| P1-8 | 自动生成会话标题 | `generateTitle + fast_model` | 用户多次开窗时方便定位 |
| P1-9 | 流式指示器（"正在生成回复"动态） | chat-iframe 当前只有 toolEvents 文本 | 用户感知不到后端在做什么 |
| P1-10 | 切回标签页续流（visibilitychange） | `AgentChatComponent.vue:2179-2182` | 防止 iframe 在切回时被切流 |

### 🟢 P2 — 体验优化（按业务诉求评估）

| # | 缺失项 |
|---|--------|
| P2-1 | 智能体切换（多智能体场景） |
| P2-2 | 会话重命名 / 删除 / 置顶 |
| P2-3 | 会话搜索 |
| P2-4 | 消息编辑 |
| P2-5 | 消息反馈（点赞 / 点踩） |
| P2-6 | 快捷键（除 Enter 发送外） |
| P2-7 | 加载 / 错误边界提示（isLoadingMessages / chat-loading） |
| P2-8 | 移动端响应式布局 |
| P2-9 | 暗色模式适配 |
| P2-10 | 国际化文案（i18n） |
| P2-11 | 发送冷却（防双击重复发送） |
| P2-12 | 草稿态模型选择迁移 |
| P2-13 | 自适应窗口尺寸（响应式布局） |

### 🔵 P3 — 文档 / 测试补充

| # | 缺失项 | 备注 |
|---|--------|------|
| P3-1 | E2E 测试覆盖 | chat-iframe 当前仅有父页面脚本 DOM 识别 + chat 链路最小契约（`chat-iframe/test/parent-script.test.js`、`chat-iframe/test/chat-api.test.js`、`chat-iframe/test/incoming-documents-api.test.js`），缺 UI 测试 |
| P3-2 | 更新 [docs/develop-guides/changelog.md](../develop-guides/changelog.md) | 登记 chat-iframe 复刻进度 |
| P3-3 | 同步 [docs/.vitepress/config.mts](../../.vitepress/config.mts) 导航 | 若新增面向用户的正式文档 |
| P3-4 | API 契约文档（OpenAPI） | chat-iframe 依赖的 8 个后端接口（chat/thread(s)、chat/attachments/tmp、chat/thread/{id}/attachments/confirm、agent/runs、agent/runs/{id}/events、system/model-providers/models/v2、incoming-documents/extractions/query）需要明确契约 |

---

## 6. chat-iframe 差异化能力（web 主站没有）

| 能力 | 实现 | 价值 |
|------|------|------|
| 来文匹配状态（matched / multiple / pending_sync / not_found） | `IncomingDocumentPanel.vue` | 来文场景核心 |
| 结构化抽取结果（按类别展示 + 原文依据 + reason） | `IncomingDocumentPanel.vue` | 来文场景核心 |
| 页面上下文作为问题上下文（"问网页"开关） | `buildChatQuery` + `pageContent` | 把当前页面内容带入问答 |
| 选中文档 + 抽取结果作为上下文（"问文件"开关） | `buildChatQuery` + `selectedFile + extractionResult` | 来文场景核心 |
| 父页面无感嵌入（DOM 扫描附件 + 悬浮按钮 + 拖拽） | `parent SDK _html + _bindEvents` | 嵌入生产系统的核心入口 |
| iframe ↔ 父页面双向 postMessage（10 种消息类型） | `useIframeBridge.ts` | 完整双向通信 |
| 父页面事件订阅（conversationCreated / messageSent / stateChange） | `parent SDK on / _emit` | 上层应用接入 |
| 父页面来源白名单（originAllowlist） | `useIframeBridge.ts:25-26` | 安全控制 |
| 悬浮窗口四态（minimized / normal / maximized / closed） | `parent SDK _setWindowState` | 桌面级 UX |
| 多附件默认选中第一项 | `iframe-context.ts:26-30` | 零点击闭环 |

---

## 7. 建议与下一步

### 7.1 推荐优先级

1. **P0 全部 13 项**应当作为下一个迭代的主目标，对应"聊天核心体验"。
   - 其中 P0-1 ~ P0-3（Markdown + 代码高亮 + 公式/Mermaid）建议通过把 `web/src/components/common/MarkdownPreview.vue` 抽出来复用，而不是在 chat-iframe 重新实现一套。
   - P0-4 ~ P0-6（reasoning / tool_calls / refs）需要重新评估：当前 chat-iframe 后端返回的是简化事件结构，可能需要扩展 `runEventHandlers` 的接口。
   - P0-7（停止生成）需要扩展 `sendMessageStream` 支持 `signal`（当前已实现 AbortSignal 透传），并在 ChatInput 加上停止按钮切换。
2. **P1 中**只挑对来文场景有价值的：P1-8（自动标题）、P1-9（流式指示）、P1-10（visibilitychange 续流）建议一起做。
3. **P2 全部**建议暂缓，与 web 主站行为对齐需要等主站稳定后再说。
4. **P3 中**的 changelog 同步与 API 契约文档应在下个迭代开始前先做，避免接口再次变更。

### 7.2 代码复用策略

- **直接复用 web 主站的 Markdown 渲染组件**：把 `MarkdownPreview.vue` + 依赖抽成独立包或 monorepo workspace，避免双份维护。
- **共用类型定义**：web 主站的 agent 相关 type（如 ChatMessage、ChatThread）可考虑同步到 chat-iframe，避免 `chat-iframe/src/types.ts` 与 `web/src/types/**` 漂移。
- **共用 SSE 解析器**：`useAgentStreamHandler` 与 `chat.ts:readRunEventStream` 都在做相同的事，建议抽取。

### 7.3 架构决策待定

| 议题 | 选项 A | 选项 B |
|------|--------|--------|
| Markdown 渲染 | 把 web 主站 MarkdownPreview 抽出来复用 | chat-iframe 重新实现一套轻量版 |
| 工具调用可视化 | 复用 ToolCallsGroupComponent | 简化为"工具名称 + 时间戳"列表 |
| 引用展示 | 复用 RefsComponent | 只展示文字摘要 |
| 停止生成 | 扩展 sendMessageStream 支持 AbortSignal | 在 iframe 层加超时与手动取消 |
| 流式事件扩展 | 扩展 runEventHandlers（onReasoning / onToolCall / onRef） | 维持当前 onText/onTool 两通道 |

建议在下一个迭代开始前开一次评审会决定。

### 7.4 验收标准

- P0 全部完成后，chat-iframe 与 web 主站聊天核心体验应**至少 90% 一致**。
- P1 全部完成后，chat-iframe 应能完整支撑生产系统嵌入场景。
- P2 全部完成后，chat-iframe 应当作为独立的轻量客户端长期运行（不再依赖 web 主站）。

---

## 8. 附录

### 8.1 参考链接

- [chat-iframe README.md](../../chat-iframe/README.md)
- [web/src/components/AgentChatComponent.vue](../../web/src/components/AgentChatComponent.vue)
- [web/src/components/AgentMessageComponent.vue](../../web/src/components/AgentMessageComponent.vue)
- [web/src/components/AgentInputArea.vue](../../web/src/components/AgentInputArea.vue)
- [web/src/components/AgentPanel.vue](../../web/src/components/AgentPanel.vue)
- [web/src/stores/chatThreads.js](../../web/src/stores/chatThreads.js)
- [web/src/stores/chatUI.js](../../web/src/stores/chatUI.js)
- [web/src/apis/agent_api.js](../../web/src/apis/agent_api.js)
- [chat-iframe/src/App.vue](../../chat-iframe/src/App.vue)
- [chat-iframe/src/stores/chat.ts](../../chat-iframe/src/stores/chat.ts)
- [chat-iframe/src/apis/chat.ts](../../chat-iframe/src/apis/chat.ts)
- [chat-iframe/src/components/ChatMessages.vue](../../chat-iframe/src/components/ChatMessages.vue)
- [chat-iframe/src/components/ChatInput.vue](../../chat-iframe/src/components/ChatInput.vue)
- [chat-iframe/src/components/ChatSidebar.vue](../../chat-iframe/src/components/ChatSidebar.vue)
- [chat-iframe/src/composables/useIframeBridge.ts](../../chat-iframe/src/composables/useIframeBridge.ts)
- [chat-iframe/public/docmind-chat-iframe-parent.js](../../chat-iframe/public/docmind-chat-iframe-parent.js)

### 8.2 文档维护

- 本次更新作者：Claude（自动分析）
- 文档维护路径：`docs/vibe/2026-07-05-chat-iframe复刻分析报告.md`
- 下次复审时机：P0 全部完成后
