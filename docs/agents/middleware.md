# 中间件系统

中间件是 Yuxi 扩展智能体运行行为的主要机制。它工作在 LangGraph Agent 的模型调用、工具调用、状态更新和文件系统访问路径上，用来把知识库、Skills、附件、子智能体、上下文压缩和运行观测接入同一条执行链路。

内置 `ChatbotAgent` 与 `SubAgentBackend` 都会在 `get_graph()` 中构建中间件列表。运行前的资源过滤不再依赖旧版运行时配置中间件，而是在创建 Graph 前由 `prepare_agent_runtime_context` 完成。

## 运行时准备

运行时准备不是中间件，但它决定后续中间件能看到什么资源。内置 Agent 创建 Graph 前会先执行以下步骤：

- `prepare_agent_runtime_context`：按当前用户权限过滤工具、知识库、MCP、Skills 和子智能体，并派生 `_visible_knowledge_bases`、`_prompt_skills`、`_readable_skills`
- `build_prompt_with_context`：基于 Context 生成系统提示词
- `load_chat_model(context.model)`：加载主模型
- `resolve_configured_runtime_tools(context)`：加载已配置的内置工具和 MCP 工具

这意味着中间件不负责重新判断“用户是否能访问某个资源”。它们消费的是已经归一化后的 runtime context。

## 内置中间件链路

当前内置 `ChatbotAgent` 的中间件顺序如下：

| 中间件 | 作用 |
| --- | --- |
| `create_agent_filesystem_middleware` | 接入沙盒文件系统、用户工作区、线程 uploads/outputs 与只读 Skills 路由，并在工具结果过大时把内容写入 `outputs/large_tool_results` |
| `save_attachments_to_fs` / `AttachmentMiddleware` | 从 LangGraph state 的 `uploads` 读取附件路径，把可读路径注入系统提示，提示模型按需使用 `read_file` |
| `SkillsMiddleware` | 注入可见 Skill 的提示段，监听读取 `SKILL.md` 后的 Skill 激活，并按依赖追加工具和 MCP 工具；知识库工具由内置 `knowledge-base` Skill 按需加载 |
| `TodoListMiddleware` | 提供待办状态，让前端状态面板可展示 Agent 运行进度 |
| `PatchToolCallsMiddleware` | 修正部分工具调用消息形态，提升工具调用兼容性 |
| `YuxiSubAgentMiddleware` | 仅主 Agent 在存在可见子智能体时挂载，提供 `task` 工具调用真实子 Agent graph |
| `OutputContinuationMiddleware` | 模型明确耗尽输出预算后执行有界的空正文重试或正文断点续写；普通请求不主动设置输出上限 |
| `ContextCompactionMiddleware` | 按最终请求预算执行上下文压缩，归档原文并维护私有 checkpoint 状态 |
| `TokenUsageMiddleware` | 写入供应商 usage 与本地估算误差包络，供下一轮准入和前端状态面板使用 |
| `ModelRetryMiddleware` | 在暂态模型调用失败时按配置重试；上下文溢出和输出耗尽不由普通异常重试接管 |

`SubAgentBackend` 使用同一组核心能力，但不会挂载 `YuxiSubAgentMiddleware`，并额外过滤 `present_artifacts`、`ask_user_question`、`install_skill` 等不适合子智能体直接使用的工具。

## 知识库工具

知识库访问能力沉淀为内置 `knowledge-base` Skill。Agent 读取 `/home/gem/skills/knowledge-base/SKILL.md` 激活该 Skill 后，`SkillsMiddleware` 会按依赖追加 `list_kbs`、`query_kb`、`find_kb_document`、`open_kb_document`、`get_mindmap` 等知识库工具。

实际可见知识库仍由 `prepare_agent_runtime_context` 根据当前用户和 Agent 配置写入 `_visible_knowledge_bases`，工具执行时只会在这批知识库中检索。`context.knowledges` 是资源范围，不是 Skill 本身。

系统不会把知识库文件树挂进沙盒。Agent 访问知识库内容应使用 `query_kb`、`find_kb_document` 和 `open_kb_document`，而不是遍历 `/home/gem/kbs` 这类旧路径。

## Skills 注入与激活

`SkillsMiddleware` 分两步工作：

1. 模型调用前读取 `_prompt_skills`，把可见 Skill 的名称、描述和 `SKILL.md` 路径追加到系统提示。
2. 工具调用后检查模型是否读取了 `/home/gem/skills/<slug>/SKILL.md`。如果该 Skill 在 `_readable_skills` 范围内，就把它写入 `activated_skills`，并在后续模型调用中追加它声明的工具和 MCP 依赖。

这种设计让 Skill 可以先作为说明可见，只有模型真正读取并激活后才扩展工具集，避免一开始就把所有依赖工具塞进上下文。

## 附件与文件系统

附件上传后会先落盘到线程文件系统，并在 LangGraph state 中记录 `uploads`。`AttachmentMiddleware` 只把文件名和可读路径注入提示词，不会把文件内容整体塞进模型上下文。模型需要查看附件时，应通过 `read_file` 读取对应路径。

文件系统中间件负责把 sandbox backend、线程 uploads/outputs、用户工作区和只读 Skills 组合成 Agent 可访问的虚拟文件系统。普通 Agent 默认使用当前 `thread_id` 作为文件作用域；子智能体使用 child `thread_id` 做 checkpoint，同时沿用父线程的 uploads/outputs，并使用子 Agent 自己的 Skills 作用域。

## 子智能体任务

主 Agent 如果配置了可见子智能体，会挂载 `YuxiSubAgentMiddleware` 并获得 `task` 工具。这个工具不会调用旧版独立 SubAgents 表，而是查找 `agents.is_subagent=true` 且后端为 `SubAgentBackend` 的真实 Agent 配置，然后启动对应子 Agent graph。

子智能体执行时会获得独立 child thread、独立 checkpoint 和 `agent_runs(run_type=subagent)` 记录；工具结果会返回 child thread ID，后续可以把该 ID 传回 `task` 继续同一个子任务。子智能体自身不会再挂载下一层 `task` 中间件，避免形成嵌套子智能体链路。

## 上下文压缩

长对话压缩由项目自有的 `ContextCompactionMiddleware` 负责。它以最终模型请求的预算为唯一边界，不使用窗口百分比或固定保留消息数。

可配置的摘要行为来自 Agent Context：

| 字段 | 说明 |
| --- | --- |
| `summary_prompt` | 摘要模型使用的提示词 |

模型缓存中的完整窗口、最低输出预留和安全缓冲会写入 LangChain model profile。每次调用均对最终系统提示词、消息和工具定义做 Unicode/JSON 感知的本地保守估算，以“完整窗口 − 输出预留 − 安全缓冲”得到可用输入预算；超预算时才压缩最早的完整交互段。最低输出预留只决定压缩阈值，不会限制模型生成。

普通工具结果的快速内联上限由模型提示预算自动计算：`clamp(prompt_budget / 16, 3K, 16K)`。
它不对管理员或单个 Agent 开放，主 Agent、SubAgent、`read_file` 和 `open_kb_document` 复用同一
运行时值。该单项上限只减少大结果进入工作集的概率，最终安全仍由完整请求预算和 L1～L5 保证。

Token 计数不会调用 `/tokenize` 或其他远程预检接口，因此不会额外延迟首 Token。新会话先使用本地保守估算；模型成功响应后，`TokenUsageMiddleware` 用供应商返回的输入 usage 记录当前请求规模桶的最大正误差。校准键绑定模型部署、地址、请求协议和模板版本；Skill/MCP 造成的工具 Schema 变化会重新计算本地基线和诊断 hash，但不会清空同一部署已经观测到的正误差包络。缺少或不自洽的 usage 不写入校准样本，仍按本地保守估算准入。

OpenAI 兼容服务的 `finish_reason=length` 不等同于输入溢出。带正文时分类为 `output_exhausted`，先提交已生成正文，再由 `OutputContinuationMiddleware` 最多执行一次断点续写；带工具调用时分类为 `tool_call_truncated`，在进入 ToolNode 前明确失败，禁止执行或自动重试可能不完整的参数。空正文且实测 provider input 超过 `prompt_budget` 时，TokenUsage 会把携带校准快照的容量异常交给 ContextCompaction，全级重算后只重试主模型一次；正数 output/reasoning usage 证明输出耗尽但没有可见正文时，由输出恢复器提高上限并按原请求重试一次。空正文又缺少可校验 usage 时分类为 `length_unverified`，不猜测为输入溢出，也不自动压缩或重试。provider 明确抛出 prompt/context too long 但没有 usage 时，压缩器会一次性处理全部安全历史后重试一次。

### 响应式输出恢复

模型调用链的预算相关顺序固定为 `OutputContinuationMiddleware -> ContextCompactionMiddleware -> TokenUsageMiddleware -> ModelRetryMiddleware -> model`。恢复器位于压缩器外层，因此提高输出上限后的调用会按缩小后的 `prompt_budget` 重新经过 L1/L2/L3/L5；压缩后仍无法准入时不会发送注定失败的请求。

普通完成响应不会被写入 `max_tokens` 或 `max_completion_tokens`。只有 Provider 明确返回 `length` 后才计算恢复上限：从调用显式上限、模型可识别默认上限、本次 provider output usage 和部署最低输出预留中取最大值并翻倍、按 1K 对齐，再受 `clamp(context_window / 4, 8K, 16K)` 和“完整窗口减安全缓冲、固定 system/tools、最小当前输入回执”的硬上限共同约束。该 8K～16K 护栏只约束第一阶段自动恢复，不是常态输出上限或管理员配置。

单个用户请求最多执行两次恢复动作，其中正文断点续写最多一次、同一模型节点的空正文重试最多一次。续写指令使用带私有标记的临时 HumanMessage，只追加到下一次 `ModelRequest`；它不会写入业务消息 reducer。压缩分组、L5 用户锚点与摘要输入都会跳过该消息，压缩 plan 提交时再次剔除，客户端流也有独立过滤，因此会话回看、用户消息台账和最终 checkpoint 均不会出现内部指令。新真实 HumanMessage 会取消异常中断遗留的恢复状态。

`output_recovery` custom event 使用 `started/finished/exhausted/failed` 状态，并仅携带恢复模式、次数、前后输出上限和新 `prompt_budget`，不包含用户正文、回答正文或工具载荷。当前前端可以忽略该事件；运行事件和 LangSmith 可用于验证恢复调用与压缩调用的先后顺序。

触发后，中间件先把将要裁剪的完整交互段写入当前线程 `outputs/conversation_history` 下的不可变 JSONL 清单，再把私有滚动摘要、最新归档路径和近期真实 survivors 通过一次 `Overwrite` 原子提交。survivors 包含最新 HumanMessage 和最近两个受保护的完整 API round，不另建历史用户原文缓冲区。普通大型工具结果会写入 `outputs/large_tool_results` 并以回执替换；已有权威来源的 `read_file` 和 `open_kb_document` 窗口只会缩小为来源回执，不会产生第二份副本。

这对知识库检索尤其重要：`query_kb`、`open_kb_document`、`find_kb_document` 等工具可能返回较长的片段、引用和文档内容。摘要保留任务结论、文件路径和待核验事项；需要原始细节时一律复用 `read_file(offset, limit)`，避免把检索原文反复卷入工作上下文。

未触发 summary 的常规模型调用不会额外清洗最近窗口内的工具结果；常规工具结果预算主要由文件系统中间件在工具返回阶段处理。

前端“上次模型输入”优先显示供应商实测总量，否则显示 usage 校准估算或首次保守估算；“消息、私有摘要、系统、工具”始终只是本地估算构成。供应商实测总量与分项估算之差单独显示为“模型协议/模板校正”，不会伪装成某个分项的精确值。

### 工具协议与历史投影

`ContextCompactionMiddleware` 的 L1/L2 不会删除完整的工具交互。L1 只收纳当前用户请求内的超大工具结果或已完成的大参数；当前用户原文是否必须外置按“固定 system/tools + 私有摘要 + 最新用户消息”的最终准入请求判断，不能只比较用户消息自身与 `prompt_budget`，否则固定开销较大时仍会落入“无安全历史段”。L2 使用与 Claude Code 相同的 API round 边界，处理最新用户消息之前、已经闭合的历史工具 round。每次投影都保留原 AI 消息、工具名称、`tool_call_id` 和 `ToolMessage`，仅把已写入线程隔离文件的正文替换为短回执，模型需要细节时按回执路径读取。

投影前会验证完整持久化消息序列：工具调用 ID 必须非空且跨 round 唯一，每个结果必须在同一 round 中恰好匹配一次；错误结果和 `ask_user_question` round 受保护，不参与投影。缺失、重复、未知或跨 round 结果会在模型调用前抛出明确的工具协议错误，避免将无效 OpenAI 工具消息发送给本地模型。当前用户请求内的早期 round 和最近两轮保护策略由后续 L3 负责，因此 P3 不会提前改变当前请求的交互内容。

L3 处理当前单条用户请求内的早期闭合 API round，仍只投影安全工具载荷；最近两个闭合 round、错误和人工确认保持完整。若 L1-L3 后仍超预算，L5 才以完整 API round 为单位生成私有 checkpoint：latest HumanMessage 及其所在 round 永远不裁剪，主模型成功后才原子替换活动 `messages` 和私有摘要。

摘要请求不接收 Agent 工具。默认摘要提示词使用九维任务/事实/文件/错误/待办/当前工作标签；管理员配置的 `summary_prompt` 可以完整定义另一种输出结构，框架只追加与字段结构无关的累计合并协议和本次输出预算，不再强制追加九维标签。该协议规定新消息默认是对旧 checkpoint 的增量：先继承仍有效的旧约束、决策、精确标识和待办，再吸收新事实；最近消息更具体本身不构成覆盖或完成，只有明确取消、替换、更正、不再需要或完成的语义才可更新旧项，完成事项从待办转入进展而不是直接擦除结果。已经由旧轮助手完成的“只回答某内容”“仅输出某格式”“本轮不要调用工具”等单轮执行方式，只保留其产生的事实或结果；除非用户明确声明后续继续执行，否则不得提升为整体目标、待办、当前工作或下一步。恢复时先由最新真实用户消息确定本轮任务和回答形式，再从摘要读取所需历史事实，不能直接执行摘要中描述压缩时旧状态的任务字段。一个 Human turn 包含大量工具调用、需要滚动分块摘要时，每个分块只在摘要模型输入内重复该段原始 HumanMessage 作为优先锚点，防止小模型在后续大工具块中丢失最早的用户硬约束、路径和标识；锚点不写入 checkpoint 或主模型消息，也不复制图片和工具正文。输出上限按部署窗口动态计算：取部署最低输出预留、窗口八分之一与 20K 上限形成摘要 cap，因此 32K、64K、128K、256K 分别可自然扩展到约 4K、8K、16K、20K，而不依赖模型名称。摘要输入再从完整窗口中扣除该输出 cap 和安全缓冲。若 provider 仍明确返回 prompt too long，只移除最旧 20% 的完整 API round、收紧输入估算并重试一次；原消息已经归档，重试不会删除不可恢复原文。

摘要 `finish_reason=length`、不遵守输出上限、普通生成失败或第二次 PTL 都不会提交退化 checkpoint，也不会调用主模型。默认九维策略标签不齐只记录 `format_unverified`，自定义策略记录为 `custom`，均不触发格式修复调用。L5 私有恢复段会要求模型在历史要求或事实不确定时先用 `ls/read_file` 检查 `/outputs/conversation_history/`，不得猜测；同时保留 `activated_skills` 对应的权威 `SKILL.md` 路径，要求模型继续相关步骤前重读。`SkillsMiddleware` 维护的激活状态和工具/MCP 绑定，以及 Todo、附件、artifact 等独立 state，不会被 `Overwrite(messages)` 覆盖。

只有请求超过准入预算或进入 provider overflow 恢复时，压缩器才会发布一个完整的 `context_compaction` 事件周期。L1、L2、L3 各发布一条 `finished` 或 `skipped` 结果，L5 的归档与摘要耗时较长，因此发布 `started` 后再以 `finished` 或 `failed` 结束；若前三级已经满足预算，L5 发布 `skipped`。同一周期通过 `cycle_id` 关联，并固定按 `sequence=1/2/3/5` 排序。

事件只包含层级、触发/失败分类、Token 前后值、消息数量、候选/保护消息数量、工具结果/参数投影数、归档/round 数量、摘要 revision 和安全的线程归档路径，不包含用户正文、摘要正文、工具参数、工具结果或 Skill 正文。`tokens_saved` 表示本级保守估算的释放量；L1 的 `input_externalized`、L2/L3 的 `tool_results_projected` 与 `tool_arguments_projected` 用于判断实际采用了哪类投影。界面只需把 `started` 视为压缩中，任何非 `started` 状态都结束等待提示，不应展示私有 checkpoint 内容。

### LangSmith 与压缩验收

项目没有维护一套专用 LangSmith callback；依赖中的 LangChain/LangGraph 与 LangSmith SDK 会在部署环境设置下自动记录 Agent、模型、摘要模型和工具调用树。内网部署按需在受保护的 `.env` 配置下列变量，并重建 `api`、`worker` 容器：

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=docmind
LANGSMITH_API_KEY=<deployment-secret>
```

LangSmith 负责查看完整调用树和模型输入输出，`context_compaction` SSE 事件负责给出 L1/L2/L3/L5 的确定性前后差值；不能只凭 LangSmith 中是否出现摘要模型调用判断前三级是否执行。当前部署已实测 LangGraph metadata 包含 `request_id` 和 `thread_id`；脚本同时输出这两个标识与 Agent `run_id`，前两者用于检索 LangSmith，后者用于查询 DocMind 运行记录和 SSE。

真实 API 验收使用 `backend/scripts/validate_context_compaction_api.py --scenario-file <json>`。脚本创建可在 chat-iframe 回看的隔离会话，串行发送各轮问题，并支持 `expect.compaction.min_values` 与 `expect.compaction_order` 校验。版本库中的 L1/L2/L3/L5 场景位于 `backend/scripts/scenarios`；脚本日志只输出有界输入/回答和上述诊断字段，大正文仍留在线程文件与 LangSmith 权限边界内。

## 自定义中间件

新增中间件时，将实现放入 `backend/package/yuxi/agents/middlewares`，再在具体 Agent 的 `get_graph()` 中加入 `middleware` 列表。新增前先确认它属于哪一种职责：

- 资源过滤、权限收敛和默认资源选择应放在 `prepare_agent_runtime_context` 一类的 Graph 创建前逻辑中。
- 模型提示注入、工具动态追加、工具结果处理和 state 更新适合做成 LangChain Agent middleware。
- 文件读写、工具结果卸载和 artifacts 展示应优先复用 `create_agent_filesystem_middleware` 与沙盒 backend。

仓库中仍保留 `DynamicToolMiddleware`，但当前内置 Agent 的工具和 MCP 加载已经由 `resolve_configured_runtime_tools(context)` 与 `SkillsMiddleware` 承担。新增功能时不要默认复用旧的动态工具中间件，除非确实需要“预注册后按请求筛选”的模式。
