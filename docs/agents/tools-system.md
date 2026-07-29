# 工具系统

Yuxi 的工具系统基于注册机制，支持多种工具类型的动态组装。

## 工具注册机制

Yuxi 的工具系统采用 `@tool` 装饰器注册机制，核心位于 `backend/package/yuxi/agents/toolkits/registry.py`。

### @tool 装饰器

```python
from yuxi.agents.toolkits.registry import tool

@tool(category="buildin", tags=["示例"], display_name="示例工具")
def example_tool(text: str) -> str:
    """示例工具：返回处理后的文本"""
    ...
```

装饰器参数：
- **category**: 工具分类，用于分组（如 `buildin`、`knowledge`、`incoming_document`、`debug`）
- **tags**: 标签列表，用于前端展示
- **display_name**: 显示名称（给人看的名字）
- **icon**: 图标名称（可选）

### 自动发现

导入 `toolkits` 包时会自动触发注册：

```python
from yuxi.agents.toolkits import buildin, mysql  # 触发 @tool 装饰器执行
```

`toolkits/__init__.py` 中已包含 `buildin`、`mysql`、`debug` 模块的导入，这些模块加载时会自动注册所有带 `@tool` 装饰器的函数。

## 工具分类

### 内置工具 (buildin)

| 工具 | 说明 |
|------|------|
| `ask_user_question` | 向用户发起交互式提问 |
| `present_artifacts` | 展示 Agent 沙盒 outputs 目录下的产物文件 |
| `install_skill` | 从沙盒路径或 Git 来源安装当前用户私有 Skill，并激活当前主智能体会话；子智能体禁用 |
| `tavily_search` | Tavily 网页搜索（需配置 `TAVILY_API_KEY`） |

Qwen-Image 生成能力已迁移为内置 Skill `image-gen`。模型调用与图片下载在 Agent 沙盒中完成，生成后的图片保存到 `/home/gem/user-data/outputs/`，再通过 `present_artifacts` 展示。

### 文档导出工具 (document)

`export_office_file` 是具有 `ToolRuntime` 上下文的原生工具，由 `office-export` Skill 或业务 Skill
按需加载。它读取当前会话中的受限 JSON 定义，生成 DOCX、PDF 或 XLSX，并支持插入当前会话图片。
SVG 只在写入 Office 文件时临时转换为 PNG，不会同时生成两个交付物。

| 工具 | 说明 |
|------|------|
| `export_office_file` | 根据 definition 虚拟路径生成 DOCX、PDF 或 XLSX |

`office-export/references` 分别维护 DOCX、PDF、XLSX 的模型输入契约。业务 Skill 只维护业务字段、
工作表和证据口径，并通过 Skill 依赖名读取 `office-export` 入口，不写入它的物理 reference
路径，也不重复维护通用 Office 格式规则。

### MySQL 工具 (mysql)

| 工具 | 说明 |
|------|------|
| `mysql_list_tables` | 列出数据库中所有表 |
| `mysql_describe_table` | 获取表结构信息 |
| `mysql_query` | 执行只读 SQL 查询 |

### 知识库工具 (kbs)

知识库工具使用 `@tool(category="knowledge")` 注册，并通过内置 `knowledge-base` Skill 的 `tool_dependencies` 按需加载。`get_common_kb_tools()` 仍可用于直接获取完整工具列表：

```python
from yuxi.agents.toolkits.kbs import get_common_kb_tools

kb_tools = get_common_kb_tools()
# 返回: [list_kbs, get_mindmap, query_kb, find_kb_document, open_kb_document]
```

| 工具 | 说明 |
|------|------|
| `list_kbs` | 列出用户可访问的知识库 |
| `get_mindmap` | 获取知识库的思维导图结构 |
| `query_kb` | 在指定知识库中检索内容，返回结构化的 `resource_id`（即 `kb_id`）/`file_id`/`chunk` |
| `find_kb_document` | 在已知文件内按关键词或正则定位内容 |
| `open_kb_document` | 按 `file_id` 分段打开知识库文档（默认窗口 1800 行） |

### 来文工具 (incoming_document)

来文工具不属于 Agent 基础 `buildin` 工具，由来文类内置 Skill 的 `tool_dependencies` 按需加载。管理员在 Agent 中配置 Skill 后，模型读取对应 `SKILL.md` 激活能力，声明的工具才对模型可见。

| 工具 | 说明 |
|------|------|
| `search_incoming_documents` | 按日期、分类、条目类型、标题、文号或附件名分页查找来文 |
| `read_incoming_document` | 读取来文级结论、附件和正式结构化结果，按需将指定附件 Markdown 写入当前会话 sandbox |
| `get_incoming_document_statistics` | 按分类、条目类型和月份统计来文文档数及结构化 detail 数 |

分类和条目类型筛选都接受稳定 ID 或当前中文名称，并在查询前统一为 ID；未知值直接返回当前支持列表，不会静默得到空结果。分类中文名称只用于界面和回答展示，调整名称不会影响数据库、Skill 或历史查询。需要原文时，先由 `read_incoming_document` 返回虚拟 `markdown_path`，再使用沙箱 `read_file` 分段读取。

Phase 3 首批业务 Skill：

| Skill | 用途 | 依赖 |
|------|------|------|
| `incoming-document` | 来文查询、统计、单篇综合解读和按附件核验 | 查询、读取、统计来文、向用户提问 |
| `build-risk-ledger` | 按时间范围生成风险汇总和 XLSX 台账 | 查询、读取、统计来文、向用户提问，`office-export` |
| `summarize-assessment-actions` | 汇总通报、考评、奖惩和后续任务 | 查询、读取、统计来文、向用户提问，`office-export` |

`office-export` 不会成为 Agent 默认能力；只有 Agent 配置该 Skill，或业务 Skill 声明依赖时，
`export_office_file` 才会向模型开放。

## 工具组装

工具组装在 Graph 创建阶段完成。内置 Agent 会先调用 `prepare_agent_runtime_context` 过滤当前用户可用资源，再调用 `resolve_configured_runtime_tools(context)` 加载已配置工具：

1. **基础工具**：从 `context.tools` 中按名称筛选
2. **MCP 工具**：根据 `context.mcps` 加载 MCP 服务器工具
3. **Skill 依赖工具**：由 `SkillsMiddleware` 在 Skill 激活后按需追加，包括知识库、来文和 Office 导出原生工具

```python
from yuxi.agents.context import prepare_agent_runtime_context
from yuxi.agents.toolkits.service import resolve_configured_runtime_tools

context = await prepare_agent_runtime_context(context, user=current_user, db=db)
tools = await resolve_configured_runtime_tools(context)
```

## Skills 集成

Skills 与工具是两种不同的扩展机制。工具是具体的功能实现，而 Skills 是包含提示词、工具依赖和元数据的完整技能包。通过 `context.skills` 配置 Skills 时，对应的技能文件会被挂载到沙盒的 `/home/gem/skills/<slug>/...`，智能体可以通过读取 SKILL.md 来了解如何使用这些技能。

关于 Skills 的详细机制，请参阅 [Skills 管理](./skills-management.md)。
