# 版本变更记录

本页用于记录各版本发布说明（新增、修复与破坏性变更）。

同一版本的多次功能更新时，应以功能为单位进行更新，比如之前添加了 A 功能的更新，在后续的更新中修复了因 A 功能引入的 bug，那么这个修复说明应该和 A 功能描述放在一起，而不是新增一条修复记录，功能更新同理。

## v0.7.1 (current)

### 小助手交互

- 修复并优化 chat-iframe 的 `ask_user_question` 反问交互：完整支持 `allow_other` 单选和多选，自定义答案按既有 `{ type, text, selected }` 协议恢复运行；内部 resume JSON 与拒绝值不再作为用户消息展示，实时完成和刷新历史保持一致。反问卡片改为中性边框层级、稳定的 40px 操作按钮和清晰的选中/聚焦/禁用状态；执行过程与工具详情补齐窄窗口宽度约束，避免默认悬浮窗口出现非必要的横向滚动。工具参数对模型统一发布为结构化问题数组，并兼容本地模型偶发产生的连续 JSON 与 `label` 题干；摘要压缩不再把已完成反问参数替换为归档回执，避免后续调用仿写内部占位字段而丢失问题。
- 将反问交互调整为替代聊天输入区的现代化底部操作面板：面板通过四周留白、完整中性边框、轻量向上阴影和顶部品牌色状态线与聊天记录清晰区分；标题和操作区使用白底，问题区使用极浅灰背景，选项改为无阴影白色描边项并统一使用 18px 自定义单选/复选控件，仅在悬停和选中时使用品牌浅色强调。标题与提示居中，问题和选项使用更紧凑的字号层级，常见双问题表单可直接完整展示；问题区域独立滚动且新问题始终从第一题开始，底部主次按钮等宽对称。发送收尾会从线程状态补齐可能遗漏的中断并阻止待回答期间创建新 run，结构化接口错误只展示具体消息，不再出现 `[object Object]`。选择已有会话并完成历史加载后直接定位到最后一条消息，页面刷新后首次展开已有长会话也会在窗口恢复尺寸后重新定位到底部，流式回答仍保留平滑自动滚动。

### 离线可视化
- 可视化渲染工具成功后会将受校验的 SVG 自动登记为当前轮交付物，不再依赖模型额外调用 `present_artifacts`；chat-iframe 的工具调用组在运行期间自动展开、全部完成后自动收起，避免长任务过程淹没正式回答。工具摘要统一按用户可见调用计数，Skill 激活不再重复计入普通工具；单调用直接显示调用行，点击后展开参数和结果，仅同一消息包含多条调用时保留分组摘要。常用内置工具显示中文名称，并为折叠控件补充展开状态。

- 新增离线可视化能力包：通过按需激活的中文 Skill 生成 CSV 数据图表、流程图和思维导图，渲染结果统一为 SVG 并复用现有 artifact 预览、下载与保存链路。渲染工具不进入 Agent 默认工具集，新用户请求会清空上一请求激活的专用 Skill；中间 CSV 和结构文件不会被自动交付。默认 Charts MCP 已退役；数据图表继续使用固定版本 ECharts SSR，流程图由受限 JSON 转换为固定版本 D2，思维导图使用带多分支配色和层级标签的 ECharts tree。API/worker 镜像构建阶段安装 ECharts、csv-parse、D2、resvg 和中文字体；项目沙盒运行时镜像固定安装 PyMySQL，MySQL 数据准备在禁网环境不再触发依赖下载。
- SVG 交付物现在通过鉴权 artifact 接口直接显示在聊天列表，保留原有预览和下载操作；大型缩略图使用稳定预览高度并可直接打开完整预览，完整预览使用更大的视口和自然尺寸滚动，避免宽流程图或长思维导图被压缩到不可读。切换会话或卸载组件时会释放临时 Blob URL。
- chat-iframe 入口 HTML 与无内容哈希的父页面 SDK 不再被浏览器缓存；最新 SDK 还会为每个 iframe 实例追加入口缓存键，内置示例页通过发布版本键避开策略生效前的旧 SDK 响应，避免嵌入式浏览器复用旧 SDK 或旧子框架文档、继续引用旧版本资源而使 SVG 内联展示未生效。
- 修复可视化渲染工具的运行时注入与自定义 Schema 冲突，避免框架内部 `runtime` 参数触发校验失败。
- 修复数据图表嵌套字段映射未转换为 JSON 数据的问题，避免真实渲染任务因 Pydantic 对象无法序列化而失败。
- 补全流程图子 Skill 的 JSON 字段契约与最小分支模板，避免本地模型在 `render_flowchart` 前反复猜测节点和边字段。
- 三类可视化子 Skill 在主流程中显式约束 `output_name` 使用 ASCII 文件名主体；思维导图同时要求把 `write_file` 返回的完整 `.mindmap.md` 路径原样传给渲染工具，避免本地模型先用中文名称或无扩展名路径触发一次无效渲染并额外消耗上下文。

### 上下文管理

- iframe 页面、附件摘要和结构化结果改为共享单一的 4000 字符总预算：分区配额在渲染入口统一计算，先保留附件定位与结构化信息，再用剩余空间内联网页；网页无法完整放入剩余预算时会落盘并提供 `read_file` 路径，不再通过最终整段截断丢失页面正文或尾部附件信息。最终请求仍超预算时，已完成工具调用的大参数会收纳到线程隔离文件，仅保留调用 ID、名称和可追溯路径，避免破坏工具调用协议；同步和异步收纳入口统一复用同一套候选计算与收益排序，只保留各自必要的文件写入方式。
- 完善预算驱动的上下文恢复：被裁剪的完整交互段会先写入线程隔离的不可变 JSONL 归档清单，并把最新路径随私有摘要原子提交；摘要模型超出目标或暂时不可用时，均按最终请求预算退化为归档回执并标记 `degraded`。模型重试不再吞掉 `ContextOverflowError` 或把它伪装为助手回复，而是交由摘要恢复后仅重试一次。
- 修复本地近似计数严重低估真实模型输入的问题：调用前对最终 system/messages/tools 做 Unicode/JSON 感知的保守估算，不请求 `/tokenize`，也不增加首 Token 网络等待；模型响应后用供应商 usage 累积当前会话的最大正误差和倍率，后续调用按校准值准入和持续压缩。空正文 `length` 会先保留实测 usage 再恢复，无 usage 的明确溢出只执行一次最大安全压缩与重试。Web 与 chat-iframe 将用量入口改为普通用户可理解的“本轮上下文用量”，明确区分实际用量与估算分类，为所有数值补充 Token 单位，并完整展示安全输入上限、可用余量和模型格式等额外开销；发生过摘要时会明确说明较早对话已压缩为“历史摘要”。
- 修复私有摘要泄露到聊天流：摘要模型调用显式标记为 `summarization`，统一流出口据此过滤内部摘要事件，避免其被误渲染为用户可见回复。
- 增加上下文压缩过程提示：摘要中间件仅发布不含摘要正文的开始/结束状态，Web 与 chat-iframe 在实际压缩期间把“正在生成回复”切换为“正在压缩历史对话”，完成后自动恢复生成提示。压缩状态覆盖归档与摘要两个阶段；修复 JSONL 归档已写入但 checkpoint 尚未提交时，下一轮幂等重试被扩展名误判为失败、导致对话无法继续的问题。
- 修复滚动摘要只关注最新任务、丢失较早已核验精确事实的问题：每次摘要显式把上一版摘要作为持久记忆合并，优先保留事实、数字、名称、标识符和未完成事项；若有限摘要未包含用户追问的旧细节，模型会从 `/outputs/conversation_history/` 的不可变历史归档核对后回答，避免依据相似条目猜测。
- 完善本地模型工具问答的可见输出：统一要求工具调用前后的必要说明跟随用户语言，并避免输出英文内部计划；文件工具仅使用已获得的绝对路径，工具失败时不得猜测事实或路径。chat-iframe 用户勾选“问文件”后，后端会在模型调用前把已解析的所选来文 Markdown 准备到当前线程，只注入原文路径而不注入 Skill 激活、业务工具或回退工作流；同一线程重复提问复用已准备文件，不重复下载。来文 Skill 仍是读取和核验业务语义的唯一归属，摘要和结构化提取会明确标示为非逐字原文。chat-iframe 会修复模型生成的 `**重点**正文` 行内强调格式，压缩状态变化也会自动滚动到可见区域。真实模型验收新增来文答案基准，关键事实错误、内部计划泄露和异常工具循环均不得判定为通过。
- 优化本地模型首个可见事件配置：模型缓存继续合并供应商与单模型的 `extra.parameters`，运行时统一透传当前模型服务支持的请求参数；Chat 模型配置改为“模型请求参数 JSON”，不再把 `reasoning_effort` 伪装成通用思考开关。不同服务应按各自协议配置（例如 OpenAI 兼容服务可使用 `reasoning_effort`，其他服务可能使用不同字段或嵌套结构），并以真实对话的首个可见事件和答案质量验收。

- 调整小助手来文原文读取边界：iframe 继续传递 `prepare_file_paths`，但服务端默认不预先物化和注入 Markdown 路径，避免基础 `read_file` 绕过 `incoming-document` Skill；Skill 改按信息充分性决定是否需要读取原文。修复精简 SSE 丢弃上下文压缩状态的问题，chat-iframe 可在压缩期间显示“正在压缩历史对话...”。
- 修复小助手运行状态与交付物同步：chat-iframe 在流式期间按主站节奏补拉线程状态，且仅在本轮 `write_todos` 改变待办后显示“本轮进度”，不会把持久化的上一轮待办误显示为新任务。删除上下文面板重复的“模型格式等额外开销”文字项，保留进度条中的“模型协议/模板校正”作为唯一解释；工具结果外置会单独说明已收纳数量，明确它不是历史摘要。模型遗漏 `present_artifacts` 时，仅将当前轮 `write_file` 成功写入的非临时 `/outputs` 文件登记为交付物；不扫描目录，也不自动发布 `edit_file`、工具缓存或历史文件。

### 开发记录
- 补齐上游同步后的端到端验收基线：真实 E2E 测试统一切换到当前 `/api/agent/runs` 与 Run 状态查询协议，附件状态用例等待异步 Run 终态；新增忙碌会话的真实排队/取消用例；知识库集成 fixture 从当前服务动态选择已启用的 embedding 模型，不再硬编码已废弃供应商；知识导图集成用例会真实上传临时文件、写入文件记录、生成并回读导图，不再因缺少文件 fixture 跳过；子智能体文件断言忽略本地模型偶发补充的中文句号，避免非语义标点导致误报失败。
- 同步上游 Agent 请求队列的基础能力：忙碌线程可显式选择 `enqueue`，排队输入会先持久化为独立请求而不写入会话消息，只有前一运行成功结束后才按 FIFO 创建 `AgentRun` 并投递 worker，避免多轮并发输入污染当前模型上下文。新增请求查询/撤销接口；失败、取消和中断会暂停后续队列，worker 重启会恢复尚未投递的运行并派发可执行的队首请求。主站 UI 接入与 Steer/审批模式仍在独立批次设计，chat-iframe 继续使用既有即时拒绝语义。
- 同步上游格式化解释器选择修复：`make format` 读取 `backend/.python-version` 并传递给 uv，避免开发主机存在多个 Python 时格式化/导入排序使用了与项目依赖不匹配的解释器。
- 同步上游中断会话恢复修复：线程状态接口会在最新主会话运行处于 `interrupted` 且 checkpoint 仍有提问时返回可恢复问题和原运行 ID；主站和 chat-iframe 切回会话后恢复提问卡片，不会重放已经终止的 SSE。状态补拉以请求版本隔离过期响应，避免已恢复运行被旧 checkpoint 响应重新标记为中断。
- 同步上游线程元数据持久化修复：更新对话 JSON 元数据时改为复制后重新赋值，确保 SQLAlchemy 能追踪变更并实际落库；原有元数据仍按增量合并保留。
- 同步上游模型选择器交互优化：聊天页可清空当前会话的模型覆盖并回退到智能体默认模型，运行时配置页可清空显式模型字段；只读配置下选择器不可再展开，切换或清空模型后会清除上一模型的状态检查结果。chat-iframe 不含模型管理和运行时配置表单，不新增对应 UI。
- 同步上游 Skill 推荐卡片说明优化：补充 `skill-creator`、前端设计、DOCX、XLSX 与 PDF 的适用场景，并将卡片描述固定为两行，避免长短说明造成卡片高度跳动；嵌入式聊天入口不含 Skill 管理页，因此不新增对应 UI。
- 同步主站与 chat-iframe 的流式消息滚动性能优化：两端自动滚动均改为监听会话显示列表引用变化，不再在每个流式字段更新时深度遍历整条消息树；iframe 的 SVG 交付物预加载仍保留深度监听，确保运行结束后补挂交付物时可被及时加载。
- 同步上游 Agent 流式对话渲染性能修复：自动滚动只监听会话列表引用变更，不再在每个流式消息字段更新时深度遍历整条会话树，避免长对话输出期间产生不必要的响应式开销。
- 同步上游后台任务执行超时保护：`Tasker` 默认最多执行 6 小时（可由 `TASKER_DEFAULT_TIMEOUT_SECONDS` 或单任务参数覆盖），超时任务会取消业务协程、记录“任务执行超时”并释放 worker；知识库解析/索引、评估数据集与评估运行、来文解析和来文入库回调均会在取消路径回写为可重试失败状态，避免遗留 `parsing`、`indexing` 或 `running` 状态。
- 同步上游知识库文件列表并发刷新修复：目录、页码、筛选或递归范围变化会推进文件浏览上下文版本；先发出的旧请求即使后返回也不能覆盖当前目录数据或错误关闭当前加载状态，避免处理中自动刷新导致文件列表抖动。
- 同步上游运行时阻塞修复：Tasker 关闭不再持有任务状态锁等待 worker 退出，取消中任务的状态落库或终态清理异常也不会让服务停止卡死；删除 Milvus 知识库时将同步 collection/图谱清理移至工作线程；线程文件目录扫描、文本读取和 artifact 媒体类型探测不再阻塞 API 事件循环。Agent 正常流式与 resume 在完成业务库预处理后释放会话连接，Neo4j 共享 driver 会在应用退出时关闭。
- 同步上游知识库初始化时序修复：生命周期已等待知识库管理器初始化时，管理器不再另起未等待的元数据加载任务；已有知识库的元数据加载完成后才允许应用继续启动，避免首个请求短暂命中“知识库不存在”。
- 同步上游知识库管理端文件名搜索：文件管理工具栏新增“搜索文件”，仅匹配文件名并直接打开文件详情；搜索接口复用数据库侧筛选、排序与计数，不影响目录懒加载和分页列表，连续搜索会丢弃过期响应。
- 同步上游知识库文件搜索修复：`search_file` 改为在 PostgreSQL 侧按文件名筛选、排序和计数，单知识库直接使用数据库分页，跨知识库仅归并当前页候选项；不再先扫描受仓储上限约束的文件列表，避免大知识库中较晚的匹配项被静默遗漏，`total` 与 `has_more` 也以完整匹配集为准。
- 同步上游超大知识库查询修复：按 `file_id` 批量读取元数据时统一按 10,000 个 ID 分批执行，避免思维导图等大规模流程超过 asyncpg 单条 SQL 参数上限而失败；返回顺序仍与调用方输入一致。
- 加固动态 Agent sandbox 网络边界：每个 Docker sandbox 独占 bridge 网络，仅允许 sandbox-provisioner 接入；API/worker 不再直连随机宿主机端口，而是通过携带独立 `SANDBOX_PROVISIONER_TOKEN` 的认证代理访问。旧共享网络 sandbox 会在下次发现时自动重建。初始化与生产管理脚本会生成并校验该 token，sandbox 相关配置不再进入运行时可修改的系统配置。基础设施端口新增 `INFRA_BIND_HOST`，默认仅监听 `127.0.0.1`，确需外部直连时可显式配置。
- 收敛公开 MinIO 图片访问：新上传的公开图片返回同源 `/minio/public/...` URL，主站、chat-iframe 与本地 Vite 均只代理该只读路径，其他 bucket 路径直接拒绝；public bucket 策略移除对象列表权限，历史 `:9000/public/...` 头像在登录、模拟登录和反馈列表返回时自动规范化，并保留查询参数与片段。
- 修复 API 镜像无法执行容器内 Lint：镜像继续安装完整测试依赖，并保留包含 Ruff 的开发依赖组，使远程 Compose 验收可以在与服务一致的 Python 依赖快照中完成“测试 + Lint”。
- 加固生产 Compose 密钥门禁：`JWT_SECRET_KEY`、`YUXI_INSTANCE_ID`、PostgreSQL、Neo4j 与 MinIO 凭据均改为生产配置解析时必填；即使绕过项目管理脚本直接调用 Compose，也不会回退到公开默认密码。`.env.template` 同步列出全部生产必填项，开发环境留空时仍沿用原有默认值。
- 修复部署中的对象存储凭据与静态文件权限：开发和生产 Compose 均将当前 MinIO 访问凭据传给 Milvus，避免自定义凭据后 Milvus 仍用默认值而启动失败；Web 生产镜像统一将静态目录设为 `755`、文件设为 `644`，防止构建产物权限过严导致 Nginx 返回 403。
- 统一账户密码最小长度：首次初始化超级管理员、创建或更新用户、创建部门管理员均由后端强制要求至少 8 个字符；主站对应表单共享同一前端常量并在提交前提示，避免仅靠浏览器校验而被直接 API 请求绕过。
- 收紧 API Key 身份绑定：未绑定具体用户的历史 API Key 不再按部门动态映射管理员身份，避免人员或角色变化导致密钥代表的主体漂移；本批不自动删除旧密钥或关联 CLI 会话，数据约束迁移将在管理员完成存量审计后单独执行。
- 同步上游测试资源清理：integration 与 E2E 会话会在前后通过公开 API 清理 `pytest`（兼容历史 `py_test`）前缀的评估运行、评估数据集和知识库；知识库删除改用接口真实返回的 `kb_id`，登录、列表或删除失败会显式中止测试，避免静默遗留污染后续验收。
- 同步上游语义分块空标题修复：Markdown 空标题不再清空已有标题层级，异常截断的标题 token 流也不会因越界中断解析；后续正文继续继承最近有效的父子标题上下文。
- 同步上游 PDF 页树预检并保持异步解析边界：PDF 进入 PyPDFLoader、MinerU 或 OCR 前会逐页确认页面对象可加载，提前返回加密、空文档和坏页槽等可操作错误；预检与正式解析一样在线程中执行，避免大文件遍历阻塞 API 事件循环。
- 同步上游 Milvus 图谱查询与删除修复：子图查询按 `max_depth` 返回完整多跳路径并将最大深度限制为 3，排除 Chunk 时约束整条路径，节点裁剪后过滤悬空边；删除单文件图谱时仅清理该文件触及且已无引用的实体，不再误删同知识库的无关孤立实体。
- 同步上游图谱构建与向量恢复：抽取、图结构写入和向量索引改为持续并发队列，单 chunk 失败会重试并持久化状态；已有抽取或结构结果可继续构建，管理端新增失败样例与向量 reconcile，并分别展示抽取、结构和向量进度。
- 同步上游代码块复制：主站助手消息与嵌入式聊天中的 Markdown 代码块均提供复制按钮和短暂反馈；iframe 复用原生复制降级，兼容嵌入式 HTTP 页面受限的 Clipboard API。
- 同步上游主站输入附件交互：聊天输入框支持直接粘贴图片和拖拽附件；图片复用已有多模态上传接口，附件先进入现有临时附件确认流程并保留解析选项。chat-iframe 已有独立的拖拽上传、图片选择和失败恢复逻辑，因此不重复改动。
- 同步上游分块策略配置收敛：主站分块策略改由知识库后端统一提供，移除前端静态副本；上传、知识库编辑和参数展示复用同一选项源，避免新增或调整策略时前后端名称、描述不一致。
- 修复小助手交付物与工具状态同步：只有 `present_artifacts` 实际成功登记的路径才会持久化到最终回答，删除按本轮修改时间扫描整个 `outputs` 的推测回填，来文 Markdown 缓存和其他工具文件不再被误显示为“本轮交付物”；补齐 PatchToolCallsMiddleware 合成工具错误的终态流事件，chat-iframe 无需切换会话即可将其显示为失败。提示词同时要求优先使用当前上下文，避免在摘要、结构化结果已足够时重复读取原文。
- 修复聊天上下文用量展示：进度条统一以模型真实窗口为上限，分项估算不足模型实测用量时以灰色“模型协议/模板校正”补齐，未使用部分保持留白；自动摘要阈值改为独立的“估算”提示；chat-iframe 的用户消息和助手消息复制统一在 Clipboard API 被 HTTP 嵌入页拒绝时降级到原生复制，且仅在实际成功后显示完成状态。
- 修复 Web 与 chat-iframe 在开发联调时因 Vite HMR WebSocket 短暂失联而整页刷新、丢失内存状态的问题：连接诊断日志确认触发链路后，默认开发 Compose 改为与正式环境共用 Vite 静态构建加 Nginx 托管；chat-iframe 仅在开发 Compose 中只读挂载示例和测试附件，生产镜像继续清理调试资源。前端改动需重建对应服务，后端热重载保持不变。
- 修复 Web 与 chat-iframe 的 Nginx 在 API 或 iframe 容器重建后继续访问旧容器 IP、导致接口返回 502 的问题：上游改为通过 Docker 内置 DNS 动态解析 Compose 服务名，并使用共享状态区让所有 Nginx worker 同步最新地址。
- 修复工具元数据加载会错误读取原始 `args_schema` 的问题：展示参数改为使用 LangChain 已排除框架注入参数的 `tool_call_schema`，兼容 Pydantic v1/v2 模型及原生 JSON Schema，避免来文读取工具的 `ToolRuntime` 中 `Callable` 字段中断智能助手对话。
- 修复本地模型在多次工具调用后空白结束的问题：模型缓存的完整窗口、最低输出预留和安全缓冲会写入 LangChain profile，主 Agent 与子 Agent 在最终请求超出可用输入预算时再压缩，而不是按窗口百分比提前触发；最低输出预留只决定压缩时机，不会截断复杂任务的实际生成，调用方显式设置真实输出上限时会相应扩大预留；OpenAI 兼容服务返回空正文且 `finish_reason=length` 时转为标准上下文溢出并进入恢复流程。来文原文模式不再重复返回整套结构化结果，已知来文与附件 ID 时直接将 Markdown 写入当前线程 outputs，并由 `present_artifacts` 交付，不再为单纯文件交付把全文读入模型上下文。
- 模型配置弹窗新增仅适用于 Chat 模型的上下文长度（Token）输入与后端取值说明；服务端统一校验为正整数并移除 Embedding、Rerank 的无效上下文配置。远端模型没有返回上下文时，管理员可按 Ollama、GPUStack 等当前推理实例的实际部署上限手动配置。
- 修正 chat-iframe 的来文附件上下文展示：多选附件分别保留摘要卡片并按问文件列表顺序展示，副附件标题始终使用自身文件名；来文管理附件摘要支持悬停查看全文，并使操作列与多行摘要顶部对齐。
- 来文管理详情接口将副附件摘要随附件对象返回；附件展开列表和详情抽屉仅展示副附件自身摘要，主附件继续复用已有来文摘要，避免重复信息。
- 收敛来文主附件与副附件的抽取边界：主附件单独完成分类和业务结构化抽取；副附件不再生成核心业务 item，改为使用无分类字段的专用提示词生成附件级摘要并随抽取运行元数据保存。chat-iframe 按选中附件分别注入上下文：主附件提供来文摘要、分类和业务条目，副附件仅提供自身摘要、文件标识和原文读取定位，多选时保留全部选中附件。

- 优化来文业务结构化抽取的来源定位：`source_quote` 调整为基于原文的模型参考片段，不再要求逐字匹配或因匹配失败丢弃业务条目；来文按附件分别抽取并为每条结果保存附件名、`source_file_id` 和全文/分段位置，小助手仅注入这些定位信息，追问细节时再按定位读取原文。管理端同步将“原文依据”改为“来源定位”，避免把模型概括误展示为逐字引用。
- 来文管理增加删除入口：列表行与详情抽屉都暴露"删除"按钮，处理中（`parsing`/`extracting`）和已入库知识库（`importing`/`partial`/`indexed`）的来文不开放删除；后端 `DELETE /api/incoming-documents/{incoming_id}` 在事务内校验并按 `incoming_documents → incoming_document_files` 与 `document_business_extraction_runs → results → items` 顺序级联清理，对应 MinIO 原文与 Markdown 在事务外尝试清理并将未删除对象写入 `minioErrors` 供后续兜底；前端用"来源单号后 6 位"作为二次确认，防止误删已确认来文。

### 开发记录

- 收紧 Agent 队列与中断 checkpoint 的边界：运行被 `interrupted` 标记为终态后，仍会等待用户回答或审批；新的 chat 请求和 worker 重启后的队首派发都会先识别该状态并保持队列暂停，避免绕过 checkpoint 将后续输入写入同一线程。恢复原运行后，既有 FIFO 队列才可继续派发。
- 修复知识导图忽略目录内文件的问题：导图候选文件改为从整个知识库的文件记录中分页读取，不再复用仅返回当前根目录的文件浏览查询；根目录为空但目录内存在文件时，仍可生成或增量比对导图。
- Agent 对话支持安全的同线程请求排队：主 Web 在当前运行仍在流式输出时可继续发送新消息，后端以持久化 FIFO 队列保存请求并在前一运行正常完成后再创建用户消息和运行任务；输入区显示等待内容与位置，支持取消，轮询到已派发 Run 后复用既有 Run SSE 接续展示。失败、取消和人工中断不会自动越过队首。`chat-iframe` 继续使用默认拒绝策略，因为嵌入端没有可展示、取消和接续队列的完整交互，保持既有忙碌提示语义。
- 收敛小助手来文上下文：提示词不再预置结构化 item 的逐字原文和分类依据；保留全部业务字段、附件清单及每个 item evidence 的附件名、原文位置与 `source_file_id`，用户需要依据或核验时由 `incoming-document` Skill 精确读取对应附件原文，降低本地模型上下文占用并避免将摘要引用误作完整原文。
- 优化来文管理附件核对与预览：来文列表标题可按需展开附件清单，附件“查看”统一进入既有详情抽屉并定位当前附件；“存入知识库”明确为“批量入库”，导入弹窗保留多选并新增附件原文预览与已选数量提示。结构化结果仅保留带文件名和位置的“原文依据”，移除重复的同段引用。Office 原文预览统一扩展至 `.doc/.docx/.xls/.xlsx/.ppt/.pptx`，复用 API 镜像已有 LibreOffice 转 PDF 能力，避免旧版 Word、Excel 等附件被误提示为二进制文件。
- 简化 chat-iframe 自助登录配置：不传 `tokenExchangeUrl` 时默认直接使用父页面传入的 `source_system`、`external_user_id` 和 `external_user_name` 向 DocMind 换取 token；移除重复的 `CHAT_IFRAME_AUTO_LOGIN_ENABLED` 开关和 `CHAT_IFRAME_ALLOWED_SOURCES` 白名单，避免新增业务系统时反复改后端配置。保留可选的 `CHAT_IFRAME_ALLOWED_ORIGINS` 和接口限流配置，用于需要时限制宿主来源和请求频率。
- 完成 Phase 3 首批来文业务 Skill：增强 `incoming-document` 的单篇来文综合解读输出，新增 `build-risk-ledger` 和 `summarize-assessment-actions`，分别支持风险台账、通报考评奖惩汇总；不再保留与 `incoming-document` 触发和流程重复的 `review-incoming-document`。三个 Skill 明确区分来文、主文件、附件和结构化事项，搜索多结果、时间范围不明确或结果规模过大时通过 `ask_user_question` 获取用户选择。分类契约统一使用 `notification`、`assessment` 等稳定 ID，摘要分类、人工纠偏、数据库、查询和 Skill 不再依赖可变中文名称；工具仍接受当前中文名称并在边界转换，未知分类直接返回支持列表，接口和前端使用动态中文标签展示。`incoming-document` 按单篇解读、列表检索和统计三个分支执行，已知来文但上下文不完整时会补读来文级详情，只有核验时才读附件全文。两个材料 Skill 采用 50 条分页、累计数量判断、空结果短路和大结果集确认，保留每个 detail 的全部附件 evidence；证据缺少 `source_file_id` 时标记待核验，不盲读所有附件。风险台账支持 XLSX，通报考评奖惩汇总支持 Markdown、DOCX、XLSX，并复用 `office-export` 的格式 references、原生 Tool 和 `present_artifacts` 交付。
- 完成 Phase 2 来文查询、读取与统计工具：新增 `search_incoming_documents`、`read_incoming_document` 和 `get_incoming_document_statistics`，支持按来文日期、有效主分类、正式条目类型、标题、文号和附件名进行文档级分页查询，并按分类、条目类型和月份统计文档数及 detail 数；搜索只返回摘要、结果类型和附件数量，读取工具返回全部附件、完整结果组和 evidence。需要原文时仅下载明确指定附件的 MinIO Markdown 到当前对话 sandbox，返回可供 `read_file` 使用的虚拟 `markdown_path`，不暴露 MinIO URL、宿主机路径或 `reader_tool`。三个工具改为 `incoming_document` 类别，并由新增内置 `incoming-document` Skill 按需加载；chat-iframe 先激活 Skill，再按“来文级结论 → 指定附件 → 原文核验”顺序调用工具。条目类型动态接受内部 ID 或当前 Schema 中文名称；查询、统计和读取仅发布当前 `ready` 来文的正式抽取结果。修复 Markdown 服务反向依赖会话服务造成的 Agent 冷启动循环导入，并补充工具输入边界、对象存储错误处理、会话 sandbox 隔离和冷启动回归测试；来文数据访问权限统一留待组织用户权限阶段处理。面向本地 Qwen 模型收敛 Skill 提示：iframe 不再硬编码 Skill 文件路径，只声明一次明确的 Skill 名称、激活步骤和附件定位参数；`SKILL.md` 使用固定顺序、单步检查和最小工具调用示例降低误调用概率。修复读取附件全文时显式参数 Schema 丢弃隐藏 `ToolRuntime` 的问题，改由函数签名原生推导工具参数，在保留输入边界校验的同时正确取得当前用户与线程。
- 完善 Phase 1 来文分类与发布一致性：摘要分类新增主分类原文依据，以及逐项包含分类名、置信度和逐字原文依据的附加分类；只有置信度不低于 0.8 且证据可在原文中定位的附加分类才进入正式抽取路由。重新上传、重跑和分类纠偏不再暴露旧结构化结果，内容变化会清空旧分类、确认和失败入库状态，已入知识库的来文禁止静默替换；空 Markdown、非法主分类和不收敛的长文摘要会明确失败。长文结构化抽取改为在合并前逐块核验并保留每段 evidence，服务重启时同步把中断来文标记为失败以便页面重试，原文件对象路径改为按附件 ID 稳定覆盖。
- 修复来文重新处理仍读取旧模型配置的问题：来文分类和业务结构化抽取统一动态跟随系统设置中的 `default_model`，移除未在界面暴露且容易与默认模型不一致的独立抽取模型配置。
- 修复本地模型不稳定遵守逐字引用要求时整份来文重新处理失败，并将结构化抽取收敛为核心信息摘要：分类、摘要与结构化抽取按不同可信边界处理。证据只使用精确匹配和确定性的 Unicode、引号、空白及独立 PDF 页码归一化，并保留原文索引；不按特定页眉、章节样式或样本内容增加匹配规则。主分类依据无法定位时不发布依据但继续分类和摘要，附加分类缺少依据时不进入额外抽取路由，结构化条目缺少依据时不进入正式结果，仅记录日志、告警和丢弃数量。整份来文能放入输入预算时统一全文抽取，不因分类或标题格式强制拆章；超长时使用通用原文上下文分块，分块不作为业务 item 边界。提示词只提取主旨、关键结论、重要责任、核心动作、时间和风险，同一主题的背景、流程、例外和实施细节合并概括，不穷举每一款或步骤；item 中的主体、数值、日期、义务和结论必须由同一段连续引用直接支持，无法共同支持时不拼接分散细节；结果不设置机械条数上限。管理要求的部门和岗位允许保留多个原文对象，周期类型的 null 按业务语义规范为“未明确”。管理页按结构化类型分组，分组内摘要和全部条目默认折叠。模型调用、JSON/schema 解析或分块失败仍阻止整份结果发布，避免把不完整抽取伪装为成功。
- 来文处理升级为文档级闭环：`incoming_documents` 改为一份来文一行，新增 `incoming_document_files` 保存主文件和附件，所有可选业务字段统一写入 `document_metadata`；上传接口改为一次提交一份来文及附件列表，附件并行解析完成后再生成来文级摘要、主分类和正式结构化结果。正式条目保留附件级 evidence（文件、原文引用和位置），管理页改为来文列表与附件展开查看，支持分类纠偏后重跑、整份来文确认及按附件预览原文/Markdown。修复增量附件误替换主文件、无变化上传仍重置重跑、处理期间附件竞态、长文提要丢失抽取分类、局部字符截断和部分 schema 失败仍发布完整结果的问题；分类纠偏会重新分析全附件抽取路由。文档分析预算改为模型上下文窗口的 70%，整份来文可容纳时一次完成分类摘要和正式抽取，超长时使用较大重叠块并合并无冲突的跨块信息；多分类抽取默认只有主分类，只保留有明确证据的第二主题和高置信度分块分类。chat-iframe 只注入一份来文级摘要、全部附件清单和 evidence，不注入附件全文，并在处理期间自动刷新状态；未入库附件原文读取明确留在 Phase 2，提示词不再暴露研发阶段名称。
- 收敛来文附件身份契约：`chat-iframe` 父页面、iframe 前端与来文摘要查询接口统一以必填 `source_file_id` 标识附件，移除 `id`、下载 URL 和文件名的身份回退；同一来文的文号、标题、类型、发文单位和日期通过 `document_metadata` 统一传入，并在未入库附件随聊天消息自动上传时提交；嵌入示例按业务上下文和来文元数据分组逐项说明，摘要卡片展示来文标题，附件清单单独标明主文件、附件、状态和 `source_file_id`。
- 新增原生 Office 导出能力：`office-export` 按格式提供 DOCX、PDF、XLSX references，业务 Skill 只通过依赖名读取其入口，不耦合安装路径或 reference 文件名；`export_office_file` 使用 `ToolRuntime` 安全解析当前会话 definition 和图片路径。DOCX/PDF 支持标题、段落、表格、图片和分页，XLSX 支持多工作表、冻结表头和单元格锚点图片。SVG 仅在写入 Office 文件时通过本地 resvg 临时转换为 PNG，最终文件直接写入当前线程 outputs；ECharts、csv-parse 和 resvg 的依赖清单上移到后端运行时，不再由可视化 Skill 目录承载。旧的内置导出 MCP、临时产物拦截器和格式专用入口已删除，通用的内置 MCP 默认配置注册与数据库同步机制继续保留。

- 修复 chat-iframe 在旧版浏览器或嵌入式 WebView 中续聊历史会话时报 `crypto.randomUUID is not a function`：本地请求与缺失消息 ID 统一在不支持 `crypto.randomUUID()` 时降级为带时间戳的唯一标识，避免聊天中断。
- 收敛 chat-iframe 父页面接入配置：iframe/API 地址和消息目标域改为从父脚本或 iframe 地址自动推导，移除失效的附件扫描、默认选中、手动初始化和前端来源白名单配置；页面信息继续自动采集，显式 `setPageContent()` 可覆盖；页面附件对象移除未使用的 `type` 字段。
- 优化 chat-iframe 附件标签操作：移除按钮扩大为清晰的 24px 点击区域，提升图标对比度，并提供悬停与键盘聚焦反馈。
- 修复 chat-iframe 刷新后普通附件不再显示：发送本轮消息时同步附件 `file_id`，使后端将 Word、PDF 等附件绑定到对应用户消息；历史恢复与图片附件保持一致。
- 修复 chat-iframe 消息反馈：点赞不再立即以空原因提交并锁定，点赞或点踩都会先展开可选反馈原因输入框，确认提交后才保存状态。
- 优化 chat-iframe 附件与交付物链路：普通附件发送时直接上传到当前线程，后端会优先把可解析的 Word、PDF、图片等文档转为 Markdown，避免模型对 `.docx` 二进制文件反复调用转写工具；生成文件时仍优先由 Agent 调用 `present_artifacts` 显式交付，若模型遗漏登记，服务端仅回填该 run 新增且不位于临时/内部目录的 `outputs` 文件，使 `.docx` 等最终文件在回答完成后自动出现下载入口，并避免把旧交付物或中间文件重复关联到新回答。

- 增加 chat-iframe 双模式 Docker 部署与统一管理脚本：统一使用 `docker/chat-iframe.Dockerfile` 提供 Vite 热更新、生产构建和 Nginx 运行阶段；开发 Compose 暴露独立 5174 端口并保留示例/测试资源，生产构建会清理这些调试文件，仅在内网启动 chat-iframe 静态服务，由 `web-prod` 在同一 80 端口代理 `/chat-iframe/`；Docker 构建固定安装项目声明的 pnpm 版本并使用 `registry.npmmirror.com`，开发容器启动时直接调用该 pnpm，避免 Corepack 直连 npm 官方源超时；`scripts/manage.sh` 和 `scripts/manage.ps1` 统一提供开发/生产环境的初始化、部署、启停、重启、销毁、状态、日志、构建和配置校验，生产首次初始化会生成必要安全配置并提示补充模型/跨域等按需变量；API 镜像 apt 源支持 `APT_MIRROR` 与 `APT_SECURITY_MIRROR` 构建参数，并在国内镜像源不可达时回退 Debian 官方源；Web 与 chat-iframe 基础镜像支持 `NODE_ALPINE_IMAGE`、`NGINX_ALPINE_IMAGE` 构建参数，默认走镜像代理，且 chat-iframe 不再额外解析 `docker/dockerfile:1` 语法镜像；修复 macOS 系统 Bash 在未指定服务时因 `set -u` 展开空数组导致 `target[@]: unbound variable` 的问题；Node 主版本统一为 24，保留 `node:24-slim` 供 Debian/glibc 的 API 使用、`node:24-alpine` 供 Web 与 chat-iframe 前端构建；独立镜像仍可接入 DocMind Docker 网络单独发布。
- 修复 chat-iframe 页面附件与交付物体验：父页面未显式选择的附件不再在 iframe 初始化或摘要刷新时自动入库，仅在用户选择“问文件”并发送问题时同步；文档摘要的结构化明细默认折叠；生成文件要求 Agent 调用 `present_artifacts` 登记，流式状态会立即挂到最终回答下方并置于来源、点赞等反馈操作之前，历史恢复兼容 OpenAI function 风格工具调用；交付物下载改用 RFC 5987 UTF-8 文件名响应头，修复中文文件名可预览但下载 500 的问题。
- 修复 chat-iframe 流式结束后的模型与上下文提示：本地流式助手消息立即显示本次选择的模型，历史回读暂未带回模型元数据时保留该名称；运行结束后主动读取最终 state，确保 token 快照可用；切换或刷新历史会话时也恢复持久化的 token 快照；上下文小窗与 web 均优先使用模型回传的 `input_tokens` 展示总量、占比和剩余量，未回传时才回退近似值；小窗复用 web 的摘要阈值优先级、1024 进位与图例色板（默认阈值显示为 100k），展示消息、摘要、系统、工具等 token 构成，消息项以“消息 (条数) Token”明确同时呈现消息数量和用量；点踩后自动聚焦原因输入框，确保在消息列表滚动区内可见。
- 增强 chat-iframe 高级运行体验：活动会话在消息流中实时展示工具调用和 Todo 进度，重新进入运行中的会话会恢复后端 state；输入区模型选择左侧按需显示最近一次模型调用的上下文用量；Agent 通过 `present_artifacts` 声明的交付物会持久化到最终回答元数据，刷新后仍显示在原回答下方，并支持带鉴权的图片/PDF/文本预览和下载，Office 文件明确仅支持下载。
- 修复 chat-iframe 高频聊天体验：用户消息左侧复制按钮在隐藏时仍可被鼠标命中，输入文字统一为 14px；移除会重复持久化历史的“重新生成（追加新一轮）”入口；快速模型生成标题失败时退回首条问题标题，并防止侧栏旧刷新结果覆盖刚生成的标题；run 结束时服务端历史尚未写全不会覆盖已显示的完整流式回答。
- 整理 MinerU 独立部署：将官方镜像构建文件、独立 Compose、`.env` 和部署说明集中到可直接复制的 `docker/mineru` 目录；主 Compose 与生产 Compose 同步引用新 Dockerfile 路径。支持通过 `MINERU_GPU_DEVICE_ID` 和 `MINERU_GPU_MEMORY_UTILIZATION` 选择 GPU、限制显存，默认后端同步为 MinerU 3.4 的 `hybrid-engine`，同时修复 Windows 下 MinerU 响应 ZIP 尚未关闭便删除导致的解析失败。
- MinerU 独立部署增加 Nginx 网关：MinerU 仅在 Compose 内网监听，宿主机只开放一个固定端口；现有 `/health`、`/file_parse` 地址不变，后续应用可按路径复用该端口。
- 修正 MinerU 独立部署验证命令：补齐 `return_images=true` 及高精度 hybrid 解析参数，避免手工验证 ZIP 缺少图片资产；补充扫描件强制 OCR、调试 JSON 和 MinerU 语义清理导致“正文缺失”的排查说明。
- 修复旧版 Office 文档解析：将 `.doc` 纳入允许上传格式，`.doc/.xls` 先通过 API 镜像内的 LibreOffice 转换为 `.docx/.xlsx`，再复用 Docling 解析；补装 `libreoffice-calc-nogui` 以支持旧版 Excel。
- 优化来文管理详情页结构化结果展示：`GET /api/incoming-documents/{incomingId}` 透出最新成功的正式业务结构化抽取 `businessExtraction` 与后端 display label，Web 详情抽屉只展示分类完成后的正式业务抽取明细；移除后端不再持久化的摘要阶段 `structuredResult` 空展示。
- 优化来文业务结构化抽取提示词与结果展示：`build_extraction_prompt` 明确每个 item 表示一个独立业务事项，同一事项的背景、依据、责任对象和要求合并到同一个 item，只有多个并列且可独立执行或确认的事项才拆成多个 items；分块抽取完成后仅在同一 schema 除 `source_quote` 外的全部业务字段一致时合并 items，并追加保留多段原文依据，避免新增 schema 时维护去重字段枚举或误合并不同事项；Web 来文详情按 schema 分组展示正式业务抽取明细，减少同一 schema 下结果过度分散和平铺卡片噪声。
- 修复 chat-iframe 文档摘要卡片只展示前 3 条结构化明细的问题：小助手改为按 schema 分组展示全部正式业务抽取 items，并使用可折叠分组承载同类结果，和 Web 来文详情的结构化结果观感保持一致；注入模型系统提示词的附件结构化信息不再按每个附件固定截断前 5 条，统一交给 iframe 上下文总长度上限控制。
- 修复删除用户后同名重建账号登录误报已注销：登录查询改为优先匹配未删除账号的 `uid/phone_number/username`，只有没有活跃账号时才返回旧注销账号提示，并同步登录框文案。
- 修复超级管理员创建用户时部门下拉可能为空：打开「添加用户」弹窗前补拉部门列表，避免用户角色状态晚于组件挂载恢复时跳过部门加载。
- 修复 chat-iframe 问文件摘要卡片在后端已有来文摘要但业务结构化明细为空时误显示“暂无结构化摘要明细”的问题：前端会展示后端摘要；当业务结构化明细存在时，同时保留摘要、分类命中和结构化依据传入 iframe 上下文。
- 优化 chat-iframe 来文结构化结果展示：后端从 `document_extraction.schemas` 导出分类、抽取对象和字段的 display label，`/api/incoming-documents/extractions/query` 随结构化结果返回，前端按后端 label 渲染文件名分类标记、抽取对象和字段名，并隐藏空字段及重复的 `source_quote` 字段。
- 修复 chat-iframe 来文摘要卡片未显示已入库元数据：`/api/incoming-documents/extractions/query` 现返回匹配来文的来源、文号、标题、类型、发文单位和时间；摘要卡片标题优先显示来文标题，仅以自动换行的分段标签展示类型、发文单位和时间，缺失值显示“无”。
- 修复来文结构化抽取重复分类导致 schema 选择为空的问题：来文摘要阶段已产生分类时，正式业务结构化抽取直接复用该分类选择抽取 schema，不再重复调用 `_classify_chunks`。
- 扩展来文通用分类与正式抽取：原“其他”分类收敛为 `DocumentCategoryResult` 中的“通用类”，仅在所有专业类别均未命中时启用，并通过新增 `general_item` 抽取核心事实、结论、说明或请求；通用类与专业类别在文档级保持互斥，避免重复结构化结果。摘要分类提示会动态注入全部类别的名称和描述并按来文主要目的判断；摘要阶段不再生成未被消费的轻量结构化对象，正式业务事实统一由 schema 抽取产生。短文档是否直接整篇抽取按近似 token 数判断，超过动态预算时先分块生成带抽取分类的临时提要，不再做局部字符截断。
- 来文管理的“重新处理”入口支持已完成来文，便于摘要成功但业务结构化抽取为空时由管理员重跑解析、摘要和结构化抽取流程。
- 修复来文与业务结构化抽取表启动建表失败：移除 SQLAlchemy 模型中与 `Column(index=True)` 重名的显式单列索引，避免 `metadata.create_all` 在 PostgreSQL 上重复创建 `ix_incoming_documents_*` 等索引导致启动事务回滚、`incoming_documents` 表缺失。
- 独立业务结构化抽取模块：将原知识库下的业务抽取迁移为 `document_extraction`，数据表统一为 `document_business_extraction_runs/results/items`；移除旧 `incoming_document_extraction_runs` 与 `knowledge_business_extraction_*` 表语义，来文解析 Markdown 后触发业务抽取，知识库普通上传不再触发业务抽取，从来文存入知识库时仅关联既有抽取结果并补齐 `kb_id/file_id`。
- 调整来文摘要提示和接口契约：`/api/incoming-documents/ingest` 仅支持 multipart 多文件上传，固定字段传入 `source_system/source_function_id/source_doc_id/files/file_metas`，其余业务字段统一放入 `document_metadata` JSON；每个文件以 `source_file_id` 独立保存和处理，初次上传未指定主文件时由后端选择首个文件，增量上传保留已有主文件；原文与 Markdown 仍写入 MinIO，PostgreSQL 仅保存地址、元数据、摘要和状态。`chat-iframe` 自动同步以 `no-store` 下载附件内容后 multipart 上传，并区分文档级 `source_doc_id` 与文件级 `source_file_id`。

- 解耦来文接入与知识库默认入库：`/api/incoming-documents/ingest` 不再依赖 `INCOMING_DEFAULT_KB_ID`，来文上传后先保存为独立 `incoming_documents` 记录并提交 `incoming_document_process` 任务；新增来文与来文抽取运行 PostgreSQL 表，查询接口改为返回 `incomingId/processingStatus/summary/hasMarkdown/knowledgeImportStatus` 等来文字段，为后续 Web「来文管理」人工存入知识库打基础。
- 接入来文解析摘要处理任务：`incoming_document_process` 读取全部已保存原文，复用现有 Parser 解析为 Markdown 并保存到 MinIO，随后通过默认业务抽取模型生成单一主分类、抽取分类集合、完整摘要与正式结构化结果，状态按 `parsing/extracting/ready/failed` 落回 `incoming_documents`，仍不触发向量化或知识库入库。
- 收敛 chat-iframe 来文上下文边界：Phase 1 只传入来文级摘要、结构化结果、附件清单和 evidence，不下载未入库附件 Markdown，也不提示模型调用尚未实现的读取工具；`read_incoming_document` 将在 Phase 2 负责按需写入 thread sandbox 并返回可供 `read_file` 使用的虚拟路径。
- 下沉知识库文档入库编排：新增 `KnowledgeDocumentIngestService` 复用“添加记录 -> 解析 -> 可选自动入库”流程，原知识库 `/documents` 入口改为调用该 service；新增管理员接口 `POST /api/incoming-documents/{incomingId}/knowledge-import`，不传 `sourceFileIds` 时默认导入全部附件，传入时仅导入选定附件；支持同一知识库内先部分入库再补齐，按附件回写 `knowledge_import_status/linked_file_id` 并聚合来文 `partial/indexed` 状态，同名附件按提交顺序精确关联。
- 补齐来文后台任务中断对账：服务启动时将异常中断的来文解析任务和附件入库任务同步回写为失败；已有附件成功入库时保留 `partial`，避免来文或附件永久停留在 `parsing/extracting/importing`。
- 新增 Web「来文管理」入口：管理员侧边栏新增来文管理页面，支持分页筛选来文、查看分类/摘要/结构化结果/解析 Markdown 预览，已入库来文可复用知识库文件详情弹窗查看原文、Markdown 与下载；入库弹窗可选择目标知识库、目标文件夹 TreeSelect、OCR 引擎和分块参数后提交知识库入库任务；失败来文支持管理员手动重试处理；后端补充来文管理列表、详情和重试接口。
- 来文详情支持原文预览：详情弹窗「原文预览」改为「原文 / Markdown」双 Tab，未入库来文也能直接预览 MinIO 上的原始文件；后端新增 `GET /api/incoming-documents/{incomingId}/file/original`，复用 `file_preview` 的 `detect_preview_type/convert_office_to_pdf/render_preview_payload` 与 `X-Yuxi-Preview-Type` 头部约定，PDF / 图片 / Markdown / 代码 / 文本等走原样预览，`.doc/.docx/.xls/.xlsx/.ppt/.pptx` 自动转 PDF，超过 30 MB 或不可预览的二进制返回受支持的提示；前端复用 `AgentFilePreview` 并按 `getPreviewTypeByPath` 挑选默认 Tab，与知识库文件详情观感保持一致。
- 优化 `chat-iframe` 页面/附件上下文注入链路：聊天 `query` 不再拼接“页面上下文/文件上下文”，改为通过 `meta.iframe_context` 传递父页面和选中附件；后端在 Agent run 执行时把上下文渲染进系统提示，短页面内联、长页面落当前线程沙箱文件并提示使用 `read_file`，已入库附件优先提供摘要和 `open_kb_document(kb_id, file_id)` 读取入口，未入库但有下载地址的附件由 iframe 下载后 multipart 调用 `/api/incoming-documents/ingest` 上传，解析未完成时只提示文件正在准备，避免模型猜测不可读内容。修复 `chat-iframe` 首条消息丢失：当用户尚无任何会话时发送问题，`send()` 先压入乐观 `user/assistant` 消息再调用 `ensureThread` 创建会话，原 `newConversation` 内部会清空 `messages`，导致主区既看不到提问也看不到回复（侧栏从后端拉历史能看到内容）。把 `newConversation` 拆成只创建 thread 的 `createThread` 和负责清空消息的 `newConversation`，让 `ensureThread` 走前者避免抹掉乐观消息，配套新增 `chat-store.test.js` 锁住该场景。
- 新增 `chat-iframe/public/example.html` 本地嵌入调试页和 5.5 章节示例附件，用于模拟外部生产系统的附件 DOM、父页面脚本、token 注入、页面上下文和 iframe 端到端聊天链路；调试页支持账号密码调用 `/api/auth/token` 换取本机 token，并通过 `/api/auth/me` 校验后注入 iframe；父脚本调整关闭/最小化交互，关闭后保留悬浮入口，悬浮入口支持拖动，拖动时临时禁用 iframe 鼠标捕获避免释放丢失；补充 README 说明宿主机调试命令以及直接打开 iframe 未注入 token 时会触发“请登录后再访问”和模型列表为空的原因；修正 `chat-iframe` 测试脚本，启用 Node 类型擦除以支持测试直接导入 TypeScript 源文件。
- 优化 `chat-iframe` 小窗布局：会话列表和新建聊天入口改为按需左侧抽屉，默认不再占用聊天区域；页面附件改为横向条展示，避免登录后普通小窗被固定侧栏挤压导致聊天内容错乱；从拖动后的悬浮入口恢复普通窗口时增加可见区域纠偏，当前位置放不下完整小助手时自动回到视口右下角。
- 优化 `chat-iframe` 会话抽屉和底部输入区：会话列表项改为更清晰的卡片行并放大重命名/置顶/删除图标按钮；移除父页面外层 `docMind 文档助手` 标题条，iframe 内顶部改为轻量工具栏并兼容从顶栏空白区域拖动普通窗口，移除易误解的常驻恢复按钮，最大化后只展示还原/关闭以保证可回到普通窗口；底部“问文件”改为附件选择弹窗，展示当前页面附件名称并支持多选，选中附件以单行摘要显示，避免多附件撑高输入框，取消全部附件时自动关闭文件上下文；本地嵌入调试页新增第二个示例附件并默认多选，便于验证摘要态；模型选择改为输入框右下角按钮与搜索弹窗，触发器和弹窗列表统一使用更小字号；回形针附件入口参考主站 `AttachmentOptionsComponent` 与 `AttachmentTmpUploadModal` 交互，先弹出“添加附件 / 上传图片”小菜单并提供 hover 提示，添加附件再进入拖拽上传弹窗，以文件卡片展示名称、已上传状态和大小后回填待发送附件，弹窗内隐藏原生文件选择控件文案。
- 优化 `chat-iframe` 文档上下文体验：结构化摘要查询结果新增前端只读上下文卡片，固定展示在聊天记录顶部，不写入后端 conversation history，也不伪装成 `user/assistant` 消息；移除顶部重复的结构化识别面板，`not_found/pending_sync/running/failed` 等状态改为中性空状态卡片并去掉冗余状态 badge，ready 与 not_found 状态统一展示“文档摘要”和带主色的附件名，ready 状态只展示摘要明细并收敛内部卡片字号；“问文件”弹窗中点击附件会同步切换当前上下文摘要，点击已选但非当前附件时只切换摘要、不取消勾选，底部不再额外重复展示文件名，并把当前上下文附件排在提交 payload 首位，后续提问仅在抽取结果 `matched + ready` 时通过 query 与 `meta.extraction_result` 注入文档摘要上下文；本地嵌入调试页支持 `mockExtraction=mixed/ready/not_found/off` 模拟 `/api/incoming-documents/extractions/query` 返回，mixed 模式按整页附件列表稳定区分 ready/not_found。
- 复刻主站聊天内容展示语义到 `chat-iframe`：用户消息改为右侧浅色气泡且不显示圆形头像，助手消息改为无边框连续正文流，不再每段都展示“助手”头像和卡片；工具调用改为主站式轻量折叠摘要，展开后按工具标题、参数行、带行号结果面板分区展示，工具型空消息不再额外显示“正在思考...”，反馈/复制等操作仅保留在最后一条完成的助手消息上，减少历史消息被拆成多张助手卡片的割裂感。
- 新增 AI 文档智能助手第一版后端闭环：补齐 `/api/incoming-documents/extractions/query` 与 `POST /api/incoming-documents/ingest`，来文来源元数据写入来文记录，附件按 `source_system/source_function_id/source_doc_id/source_file_id` 精确匹配并返回 `matched/multiple/pending_sync/not_found` 与 `ready/running/not_found/failed`；来文业务结构化抽取已迁移为独立 `document_extraction` 模块，当前不再依赖 `INCOMING_DEFAULT_KB_ID` 或知识库解析后抽取任务；`BUSINESS_EXTRACTION_MODEL` 仍作为摘要和业务抽取默认模型配置。
- 新增 `chat-iframe` 第一版前端壳层与结构化结果展示：提供独立 Vue/Vite/TypeScript/pnpm 项目和 `public/docmind-chat-iframe-parent.js` 父页面脚本，支持 iframe 创建、最小化/恢复/最大化/关闭、拖动、`setPageContent()`、`setFiles()`、`addFile()` 以及 `IFRAME_READY/REQUEST_PAGE_CONTENT/REQUEST_FILE_LIST/INIT_CONFIG/PAGE_CONTENT/PAGE_FILES_UPDATED/FILE_LIST/WINDOW_STATE` 消息；父脚本不再自动扫描宿主页面 DOM，附件统一由外部系统调用 `setFiles()` 传入。iframe 页面接收附件后默认选中第一个，自动调用 `/api/incoming-documents/extractions/query`，展示匹配状态、`ready/running/not_found/failed`、分类、结构化明细和原文依据。阶段八接入聊天能力：新增 `chat.ts`、`models.ts`、`chat` store 与 `ChatSidebar/ChatMessages/ChatInput` 组件，复用 `/api/chat/thread(s)`、`/api/agent/runs`、`/api/agent/runs/{runId}/events` 和 `/api/system/model-providers/models/v2`，支持新建/切换会话、历史消息、流式回答、工具事件摘要、模型选择、输入框附件、默认开启“问网页/问文件”，并把页面摘要、选中文档和抽取摘要同时拼入 query 与写入 meta。补齐 web 聊天核心体验的轻量复刻：新增 Markdown/代码/KaTeX 渲染、推理过程折叠、结构化工具调用、停止生成、重试、复制、点赞/点踩反馈、图片上传、附件预览、会话重命名/删除/置顶，并扩展 SSE 解析以兼容 `message_delta/tool_call/tool-finished/error/end`。补充 `chat-iframe/Dockerfile`、`nginx.conf` 支持独立 Docker + Nginx 部署，并重写 `chat-iframe/README.md` 记录原理、通信协议、参数、命令、使用和部署方式。
- 优化任务中心（Tasker）定位为「后台作业实体 + 只读进度面板」。前端修正失效的任务类型标签、状态判断收敛、任务详情补充参数/结果，并把轮询收敛到 store 修复抽屉关闭后角标不更新；后端 `TaskContext` 暴露 `payload` 消除私有穿透，进度更新按增量节流降低写放大，新增终态任务保留上限自动裁剪内存与数据库，`_load_state` 恢复历史任务使任务中心重启后仍可见。
- 知识库访问能力迁移为内置 Skill：新增 `knowledge-base` Skill，绑定 `list_kbs`、`query_kb`、`find_kb_document`、`open_kb_document`、`get_mindmap` 等知识库工具；内置 Agent 不再默认挂载知识库工具，改为读取并激活 Skill 后按需加载，同时保留 `knowledges` 作为知识库资源范围与权限边界。Agent 配置页在启用知识库但显式未选择 `knowledge-base` Skill 时实时展示提示，保存时不阻断。修复 Skill 依赖工具的可执行性：`create_agent` 中「模型可见工具」与「ToolNode 可执行工具」是两套，仅靠 `awrap_model_call` 动态追加工具只会绑定给模型、不进 ToolNode，导致激活 Skill 后调用 `list_kbs`/`query_kb` 报 `not a valid tool`；现由 `resolve_configured_runtime_tools` 统一把所有可见 Skill 依赖的本地工具随基础工具一起注册进 ToolNode（可执行），`SkillsMiddleware` 运行期再按 Skill 激活状态门控模型可见性（保持按需加载）。新增 `search_file` 工具支持按文件名关键词跨/指定知识库搜索文件，并已加入 `knowledge-base` Skill 的依赖工具；其分页统计基于全量扫描结果计算 `total`/`has_more`，避免按 `limit+offset` 截断导致计数失真。
- 增强知识库工具结果豁免：`open_kb_document` 工具结果加入 Summary 卸载豁免名单，避免大文档窗口被摘要后丢失上下文。
- 新增 Yuxi Python CLI 首版底座：新增独立 `packages/yuxi-cli` 包，提供 `remote add/use/list/ping`、`login --browser`、`login --api-key`、`whoami`、`status`、`logout`；配置统一写入 `~/.yuxi/config.toml`，remote URL 只保留实例入口并派生 `/api` 请求路径。后端新增 `/api/auth/cli/sessions` device flow 授权接口与 `cli_auth_sessions` 持久表，浏览器确认后为当前用户创建一次性返回的 API Key；新增公开 `/api/system/discovery` 声明服务端版本、API 前缀、CLI 能力和关键端点，CLI 登录前校验服务端版本至少为 `0.7.1`（`0.7.1.dev*` 按 release tuple 兼容）及对应能力；前端新增 `/auth/cli/authorize` 授权确认页。补充 CLI 本地单测与后端服务/路由单测。
- 安全与健壮性加固：token 兑换接口改为 `POST /api/auth/cli/sessions/token`，`device_code` 改走请求体，避免凭据出现在访问日志的 URL 路径中；兑换与批准会话时对会话行加 `with_for_update` 行锁，防止并发/重试导致重复签发 API Key；CLI 浏览器登录轮询区分瞬时错误（网络层错误、5xx）与终止错误，瞬时错误继续重试而非中断整个登录；`config.toml` 以 `0600` 原子创建并对名称等写入值做引号/反斜杠转义，避免明文凭据短暂可读及特殊字符破坏配置；API Key 认证在绑定用户失效时改为直接拒绝，不再 fallback 到部门管理员或 superadmin，创建 API Key 时校验部门与关联用户一致，用户软删除会同步禁用其 API Key；Dashboard 管理接口与前端入口改为仅 superadmin 可访问；用户软删除脱敏名改用用户主键生成，避免短哈希碰撞触发唯一索引冲突；前端授权页新增确认提示与对结构化错误 `detail` 的兼容渲染。
- 收敛 API Key 生成逻辑：移除独立 API Key 生成服务，统一通过 `AuthUtils.generate_api_key()` 生成 CLI 授权与用户管理中的 API Key。
- 收敛认证模块命名：CLI 浏览器授权路由合并到 `auth_router.py`，授权会话服务迁移到 `auth_service.py`。
- 为 CLI 知识库上传补齐后端接口边界：discovery 新增 `cli.kb_upload` 能力声明；普通文件上传接口在传入 `kb_id` 时先校验知识库存在且支持文档，校验通过后才读取文件或写 MinIO；新增同步 `POST /api/knowledge/databases/{kb_id}/documents/add`，用于把已上传的 MinIO 文件添加为知识库文档记录但不解析、不入库、不进入 Tasker；新增 `GET /api/knowledge/databases/{kb_id}/documents/exists?filename=...`，用于上传前按文件名或相对路径检查知识库内是否已有同名文件；旧 `/documents` ingest 入口保留兼容，但在 enqueue 前补充空 items、非 MinIO URL 与缺失 content hash 的请求级校验。
- 新增 `yuxi kb upload` 上传命令：默认仅包含 `.md/.txt/.docx/.html/.htm`，省略 `--kb-id` 时会从 remote 拉取并只展示支持文档上传的知识库，支持非全屏的方向键单选知识库与多选文件类型；支持 `--include-ext/--exclude-ext` 与 `--concurrency` 控制本地并发队列，并发默认 10、上限 300；交互终端上传阶段显示进度条，非交互输出保留文本进度；每个并发单元默认会先按相对路径调用 `/documents/exists` 检查知识库中是否已有文件，存在则直接跳过，传入 `--force-upload-file` 时跳过该预检并完全依赖上传接口的重复文件校验；单文件上传成功后立即调用 `/documents/add` 添加该文件记录，不触发解析/OCR/入库；目录上传通过 `source_paths` 保留相对路径，后端创建文件记录时使用该路径作为展示文件名以保持前端目录层级；上传接口返回“同内容文件已存在”时按已上传过跳过，不再作为错误展示；大批量上传调度改为有界提交，避免数十万文件时一次性创建全部 future 导致资源峰值过高。
- 发布 `yuxi-cli` 到 PyPI，并新增 GitHub Release 触发的 PyPI Trusted Publishing 工作流；文档新增命令行工具使用说明；CLI 运行访问 remote 的命令前会先输出当前 CLI 版本、remote 名称和 URL。
- 修复知识库文件入库/解析成功却被统计为失败（#793）：成功的文件元数据会固定携带 `error: None`，而后台任务此前以「结果中是否存在 `error` 键」判定失败，导致成功项也被计入失败数并在全部成功时仍抛出「处理完成，失败 N 个」。改为统一通过 `_is_failed_item` 按「显式 `status == failed` 或非空 `error`」判定，覆盖入库、解析、单独解析/入库三处统计。
- 优化知识库文件列表状态流转与文件预览边界：`uploaded/parsed/error_parsing/error_indexing` 状态分别展示解析、入库或重试操作；源文件预览与解析后的 Markdown 查看分离，txt/图片/Markdown/HTML/PDF/代码类按源文件类型预览；Office 源文件支持 `.doc/.docx/.xls/.xlsx/.ppt/.pptx`，点击预览时按需生成并缓存 PDF 预览内容，由同一个预览接口直接返回，不再把解析 Markdown 产物当作源文件预览。
- 优化大规模知识库文件列表加载：知识库详情接口默认不再返回全量 `files`，新增按 `parent_id/path_prefix/page/page_size/status` 查询的轻量文件列表接口；前端文件管理页改为目录懒加载与服务端分页，后端按 `source_path`/路径型文件名聚合虚拟目录，列表项只保留交互所需字段，顶部统计改用后端聚合结果，避免数十万文件场景下前端全量建树和传输压力。工作区知识库文件浏览统一改用同一套分页懒加载查询，支持真实目录和虚拟目录页码分页，非文档型知识库不再出现在工作区文件源中；文件浏览组件和后端列表接口均不再承载文件名搜索，后续搜索能力由独立后端接口和组件实现；文件列表展示抽出共享 `FileBrowserTable`，知识库详情和工作区共用展示层，并移除原知识库文件列表拖拽移动入口。
- 优化知识库启动元数据加载：服务启动时不再把全部 `knowledge_files` 记录加载进 `self.files_meta`，文件解析、入库、预览、下载、打开内容等单文件操作改为按 `file_id` 从数据库懒加载；文件状态流转改为通过数据库窄字段更新和状态条件更新完成，移除进程内处理队列修复逻辑，避免 api/worker 多进程下出现虚假的状态修复；文件统计刷新改用数据库聚合，文件大小补全从启动阶段移入显式统计修复任务，并收敛处理参数合并日志，避免大规模文档场景下启动内存和日志压力随文件数线性放大。
- 调整知识库待处理统计卡行为：文件管理顶部“待解析/待入库”统计卡从状态筛选改为提交对应后台处理任务；新增按待处理状态批量解析/入库接口，任务内按 500 条游标分页读取文件 ID，避免前端一次拉取和提交海量 ID；显式选中文件解析/入库接口增加 1000 个 ID 的单次上限。
- 修复大规模知识库统计修复失败：`repair_missing_file_stats` 不再对未入库文件查询 chunk 表，未入库文件残留的 chunk/token 统计会归零；chunk repository 的批量 `IN` 查询统一分批执行，避免 asyncpg 单条 SQL 参数超过 32767。
- 优化思维导图构建接口设计，支持增量构建和更新：新增 GET /mindmap/diff 接口检测文件变更，POST /mindmap/generate 新增 incremental 参数支持增量更新；纯删除场景无需 AI 调用（递归树手术），新增文件时 AI 整合进现有分类结构；思维导图文件加载改为显式 repository 查询，增量 diff 会按已追踪 file_id 补查分页外文件，避免把分页文件列表误当全量文件集；前端导图 Tab 新增"增量更新"按钮和变更数量 badge
- 优化文档结构与智能体运行说明：项目简介去除对 LangGraph 具体版本的强调；中间件文档按当前内置 Agent 链路重写，补充知识库工具、Skills 激活、附件/文件系统、子智能体 task、Summary 上下文压缩与工具结果卸载机制；知识库文档补充知识导图与示例问题生成机制；Langfuse 集成文档从“智能体开发”移动到“高级配置”分组。
- 移除知识库普通上传接口遗留的 `allow_jsonl` 参数，上传类型判断统一依赖 `SUPPORTED_FILE_EXTENSIONS`；评估数据集 JSONL 继续通过独立评估接口上传。
- 修复 Dependabot esbuild 告警：web 与 docs 统一锁定 `esbuild@0.28.1`，docs 同步升级 Vite/Vue 插件 override 并固定 pnpm 版本，避免旧锁文件继续解析到存在漏洞的 esbuild 版本。
- 修复 CORS 与依赖安全告警：后端 CORS 改为通过 `YUXI_CORS_ORIGINS` 配置允许来源，开发环境默认仅允许本机前端端口，生产环境未配置时不开放跨域，显式使用 `*` 时会关闭 credentials；同步刷新前后端锁文件，将 `aiohttp`、`cryptography`、`langchain`、`langchain-anthropic`、`pypdf`、`python-multipart`、`starlette`、`pyjwt`、`torch`、`torchvision`、`dompurify`、`js-yaml`、`markdown-it`、`vite` 升级到安全版本。
- 修复添加/编辑 MCP 弹窗中环境变量无法新增的问题：环境变量编辑器存在 rows -> object -> rows 的双向同步回环，`modelValue` 变化时会完全根据已有 key 重建行，导致只填了 key 的行（含刚点击「添加变量」生成的空行）被过滤掉而无法新增；现在仅当传入值与组件自身 emit 的内容不一致时才重建行，避免回声覆盖未填 key 的行。
- 修复模型与知识库后端导入循环：`yuxi.models` 改为惰性导出模型选择函数，知识库可见范围和知识库工具延迟读取全局 `knowledge_base` 实例，避免单测、热重载或轻量导入知识库包时因模块尚未完成初始化而失败。
- 修复知识库创建权限持久化一致性：创建知识库时由 Manager 归一化 `share_config/created_by` 后作为受控记录字段随首次知识库元数据插入写入数据库，避免先插入基础记录再二次更新权限字段产生短暂不一致。
- 修复 HTML 预览 iframe 高度问题：侧边预览模式改为 `height: 100%` 适应父容器，避免底部内容裁切；全屏预览模式移除 `min-height: calc(80vh - 40px)`，避免短内容下方白边；iframe 设为 `display: block` 消除行内基线间隙导致的底部白边；全屏渲染改用独立 `srcdoc`（不注入 `zoom`）按 100% 显示，侧边预览仍保持 0.75 缩放。
- 对话消息图片支持点击全屏预览：对话中用户上传的图片支持点击放大查看，复用文件预览的全屏蒙层交互（Teleport 蒙层，点击图片/空白处或按 Esc 关闭），不引入额外依赖。
- 新增 Agent token usage 状态快照，在状态面板中作为普通可折叠分组展示完整 `messages`、当前传给 LLM 的 `messages`、system/tools 构成、输入构成堆叠条和上下文窗口占用估算。
- 重构 Agent 上下文预算：删除 70% 摘要触发线和 15% 保留比例，模型配置显式保存完整上下文、最低输出预留和可选安全缓冲；调用前按最终系统提示词、工具定义与消息计算可用输入预算。项目自有摘要中间件仅在超预算时压缩完整历史交互段，并将摘要保存为私有状态，不再依赖 DeepAgents 的私有摘要实现。
- 评估数据集自动生成支持断点续跑：生成过程中按 `YUXI_DATASET_PERSIST_BATCH_SIZE`（默认 1）批量持久化已生成的题目，任务失败或中断后可从已持久化进度继续生成；新增 `POST /api/evaluation/databases/{kb_id}/datasets/{dataset_id}/resume` 接口与前端"继续生成"按钮。修复生成器先收集后产出导致批量持久化在生成中途不生效的问题：改为 worker 产出即流式回报、消费端按 attempt_no 重排输出，异常或取消时已产出未落库的题目（含队列中未消费与 buffer 残余）一并保存；恢复接口改用原子化入队，消除并发恢复创建重复任务引发的唯一约束冲突。失败数据集支持查看已持久化题目：数据集详情接口状态限制放宽为 completed/failed 白名单，前端放开失败数据集的点击查看，下载与发起评估仍仅限生成完成。
- 收敛普通聊天模型加载链路：`select_model` 保留旧 `.call()` 调用契约，内部改为通过 LangChain chat model adapter 复用 Agent 侧模型加载器，统一 OpenAI-compatible、Anthropic 与 Gemini 等 provider 的运行时适配；移除旧 `OpenAIBase` wrapper，默认重试策略迁移为 LangChain provider 参数。
- 统一 Redis 客户端管理：新增 `yuxi.storage.redis` 作为 Redis 配置、短生命周期同步客户端、共享异步客户端与 ARQ RedisSettings 的唯一基础设施入口；运行队列、系统配置快照同步、模型缓存和 worker 不再各自散落读取 `REDIS_URL` 或直接创建 Redis 客户端，Redis 连接失败日志统一使用脱敏 URL。
- 新增系统配置 Redis 快照同步：管理员保存配置时仍以 `saves/config/base.toml` 作为唯一持久化来源，成功写入后将可运行时同步的公开配置字段写入 `yuxi:runtime_config`；API 与 worker 进程在启动时各拉起一个后台同步线程，按 5 秒间隔从快照刷新内存值，读取端按普通属性访问、无需感知，Redis 不可用时继续使用当前内存值。`save_dir` 是启动期内部路径配置，不在管理员配置中展示、不从 `base.toml` 读取、不写入 Redis 快照且不支持通过管理员配置接口修改；sandbox 相关配置仍属于启动期敏感配置，运行中的已初始化组件不承诺完整热更新，修改后仍需重启保证生效；移除已无运行时调用点的 `enable_reranker` 与 `default_agent_id` 配置字段。
- 优化 FastAPI 请求链路并发能力：Milvus 知识库检索中的同步 embedding、向量/BM25/混合检索调用，以及图谱查询中的同步 Milvus/Neo4j 读操作（含连接建立）统一通过有界 `asyncio.to_thread` 在线程中执行，避免阻塞 API 事件循环；并发上限按事件循环懒加载信号量控制，不改变检索默认行为与参数上限。
- 改进 OpenAI 兼容提供商流式工具调用兼容（替代 v0.7.0 的按 provider 禁流式处理）：根因是 LangGraph v3 流式累积对 tool_call 字段“后值覆盖”，SiliconFlow、阿里云百炼等在参数续片里把 `name`/`id` 下发为空字符串覆盖首片真实值。改为 `_ToolCallChunkFixChatOpenAI` 把续片空串 `name`/`id` 归一化为 `None`，对所有 OpenAI 兼容 provider 通用生效且保留流式，移除原 `_NON_STREAMING_TOOL_CALL_PROVIDERS` 名单。
- 新增 Agent 评估运行入口：`POST /api/agent/eval/runs` 会创建正常对话与 AgentRun，复用 worker 执行链路，并以 `agent_evaluation` 标记写入 conversation、AgentRun 与 Langfuse trace；接口阻塞至运行结束后直接返回最终结果（状态、最终 assistant 输出、Langfuse trace id）。`yuxi-cli` 新增 `yuxi agent eval` 命令，用于从 Langfuse 数据集读取输入并回传实验输出
- 对话消息点赞/点踩反馈接入 Langfuse score：本地 `MessageFeedback` 保存成功后，如助手消息已关联 Langfuse trace，则同步写入 `user-feedback` score，点赞为 `1`、点踩为 `0`，点踩原因写入 comment，便于在 Langfuse 中按用户反馈筛选 trace。
- 下沉 AgentRun 基础能力：将「读取某个 run 的最终结果」（`get_agent_run_result`/`load_agent_run_result`，含状态、最终 assistant 输出、Langfuse trace id 与错误）与「阻塞至 run 终结再取结果」（`await_agent_run_result`，复用有限事件流、无额外轮询）提升进 `agent_run_service`，供 chat/eval 及未来定时任务统一复用；eval 运行入口改为非流式复用该能力（不再做 SSE 封装），移除其私有结果构建逻辑（结果不变）。
- 重构 AgentRun 接口底座：`agent_run_service` 拆出内部 `create_agent_run`、`enqueue_agent_run` 与 `request_cancel_agent_run`，保留现有 `/api/agent/runs` 行为并新增 `/api/agent/runs/{run_id}/result` 结果读取接口；`AgentRunRepository` 增加按 `parent_agent_run_id` 查询 child run 的能力，为后续异步 subagent 生命周期控制预留统一入口。
- 修复子智能体流式事件兼容：Yuxi task middleware 的 DeepAgents 子智能体 transformer 改用专用 `yuxi_subagents` projection，避免与 LangChain `create_agent` 默认注册的 `subagents` projection 冲突导致运行流式消息时报错；子线程路由收集优先读取 Yuxi projection，并保留原 `subagents` fallback。
- 宿主机端口集中可配置：把所有宿主机端口（redis 6379、minio 9000/9001、milvus 19530/9091）和浏览器打开 MinIO / Milvus 控制台的链接统一收敛到 `.env`，通过 `REDIS_HOST_PORT` / `MINIO_API_HOST_PORT` / `MINIO_CONSOLE_HOST_PORT` / `MILVUS_GRPC_HOST_PORT` / `MILVUS_HEALTH_HOST_PORT` / `VITE_MINIO_CONSOLE_URL` / `VITE_MILVUS_WEBUI_URL` 一处调整即可同步 docker-compose 端口映射、API 预签 URL 和前端跳转链接，便于与同机其他项目共享端口时快速避让；`init.sh` / `init.ps1` 中 `ensure_port_env` 改为幂等追加并在 main 末尾统一调用一次（移除两个分支中的重复调用点），`docker-compose.prod.yml` 中移除 prod 未暴露的内网端口透传避免误导。
- 收敛 `.env` 配置来源：`init.sh` / `init.ps1` 改为以 `.env.template` 为单一蓝本生成 `.env`（`.env` 不存在时直接 `cp` 蓝本），新增 `ensure_env_var` / `Update-EnvVar` 原地更新 helper（区分"已填真值 / 模板占位 / 不存在"三种情况幂等处理），删除 `ensure_jwt_env` / `ensure_port_env` 等散落写入函数。后续新增环境变量只需改 `.env.template` 并在 init 脚本里加声明，避免多处默认值脱节；`init.sh` 生成 `.env` 后会把 CRLF 归一化为 LF，避免 Linux 下 vim 显示 `^M` 或部分解析器读入行尾 `\r`；清理空模板占位时保留 `JWT_SECRET_KEY` / `YUXI_INSTANCE_ID` 的自动生成槽位，并在交互输入前直接生成随机值，避免从蓝本生成 `.env` 后因可选项或后续流程中断留下空密钥；`init.ps1` 保留 UTF-8 BOM 以兼容 Windows PowerShell 5.1 直接执行。
- 移除主界面侧边栏左下角的 GitHub 点赞数展示：`AppLayout.vue` 不再调用 `https://api.github.com/repos/xerrors/Yuxi` 拉取 stargazers count，也不再渲染「欢迎 Star」链接块和相关样式；同步精简冗余的 `.foo` 包装层并去掉 user-info 的多余底部 margin，让聊天列表可以多占用 GitHub 释放出的高度。
- 登录页收敛为单卡片纯表单：移除 `LoginView.vue` 顶部品牌导航、左侧背景图、协议同意 checkbox 与底部 footer 区域，仅保留登录/初始化表单与服务状态提示；`/` 路由重定向到 `/login`，`OIDCCallbackView` 与 `LoginView.handleInitialize` 成功路径同步改成 `/agent`；同步删除静态资源 `web/public/login-bg.jpg` 与不再使用的 `BlankLayout` 路由包装、`useInfoStore` / `computed` 等 import；同时清理 `info.template.yaml` 中 `organization.login_bg` 字段避免指向已删除文件。功能行为不变（协议同意仅是 UI 警告，不影响登录/初始化业务）。
- 品牌本地化收敛：`AppLayout.vue` 侧边栏顶部品牌区点击跳转从 `/` 改为 `/agent`，避免品牌点击后被路由守卫再次弹回登录页；`backend/package/yuxi/config/static/info.template.yaml` 中组织名 `江南语析` 与页脚 copyright 全部替换为 `DocMind`；`web/public/avatar.jpg` 替换为 720×720 JPG 品牌头像（用户提供 2048×2048 PNG 设计稿，缩放并转换得到）。

## v0.7.0 (2026-06-13)

### 破坏性变更

- Provider 与模型配置收敛：移除旧版 v1 模型配置与 Ollama 支持，运行时模型统一使用 `provider_id:model_id` 与独立 provider 模块；自定义 provider 实现逻辑从文件移动到数据库，并从 config 文件迁移到 provider 模块。
- 智能体运行时语义收敛：用户可见的 `AgentConfig` 收敛为数据库持久化的一级 `Agent`，内置 Python Agent 改为智能体后端；聊天、运行任务、恢复审批和文件预览均从线程绑定的 Agent 解析运行时上下文，前端只提交 `agent_id`。
- 知识库能力边界收敛：移除 Upload 与 LightRAG 知识库/图谱能力，知识库类型收敛为 Milvus 与只读连接器；知识库 API 统一使用 `/databases/{kb_id}/xxx` 形式，并整合 mindmap / eval 等子接口。
- Agent 资源默认选择与权限过滤：未显式配置工具、知识库、MCP、Skills、子智能体时默认启用当前用户可访问/可用的全部资源，显式选择后按允许列表过滤；Agent 创建前统一完成最终资源权限过滤、知识库 `kb_id` 可见范围派生和 Skill prompt/readable 依赖闭包派生。
- Skill 安装与权限模型收敛：Skill 元数据使用 `source_type/share_config/enabled` 表达来源、生效范围与启用状态；内置 Skill 启动或同步时自动写入数据库并默认全局启用，上传和远程添加统一改为解析草稿后确认安装，不保留旧直接安装兼容路径。
- 历史兼容层精简：移除 sandbox provisioner `local` 后端别名、ask_user_question 单问题旧协议、JWT 历史默认密钥特殊判断、内置 Skill `SKILLS.md` 文件名回退、运行事件数字 seq 兼容和前端旧字段回退。
- 用户身份命名收敛：原业务登录标识统一改为 `uid`，Agent/LangGraph runtime、conversation、agent_run、sandbox 路径和前端用户态均使用字符串 `uid`；`user_id` 仅保留给外部响应中的数值 `users.id` 或真实外键场景。

### 开发记录

- 发布版本号更新至 `0.7.0`，同步 package、Docker 镜像标签与快速开始分支引用。
- 新增内置「深度研究」多智能体：编排器 Agent（`deep-research`，ChatbotAgent 后端）负责澄清、拆解、并行调度子智能体与综合成稿，配套两个子智能体 `research-explorer`（围绕单个子问题多轮检索网页/知识库并返回带引用发现）和 `fact-verifier`（对抗式核验关键论断、标注冲突与置信度）；完整研究方法论沉淀为新增内置 Skill `deep-research`（依赖 `tavily_search`），编排器运行时读取并据此调度。三者随 `lifespan` 启动通过 `AgentRepository.ensure_deep_research_agents` 幂等落库（已存在不覆盖管理员修改）。
- 新增内置 `general-purpose` 通用任务子智能体：使用 `SubAgentBackend` 与空运行配置，作为 `task` 工具的通用委派目标，由启动初始化自动写入数据库。
- 收敛 MCP 创建与编辑入口：前端移除整段配置文本入口和模式切换器，仅保留表单字段提交；后端 MCP 创建/更新请求拒绝额外配置字段，避免绕过表单约束。
- 调整内置 MCP 默认项：移除 `sequentialthinking` 的系统内置同步，启动同步时清理历史系统内置记录，保留用户手动创建的同名 MCP。
- 图片生成能力迁移为 Skill：Qwen-Image 从内置 Python 生成工具迁移到内置 Skill `image-gen`，模型调用与图片下载在 Agent 沙盒中完成，生成结果保存到 outputs 并通过 `present_artifacts` 展示，为多图片生成模型接入复用同一产物展示链路。
- 优化前端头像加载兜底：用户与智能体头像优先展示已配置图片，加载失败后回退到基于 ID 的 DiceBear 默认头像；离线或默认头像不可达时显示名称前两个字和稳定背景色。
- 降低知识库路由与工具模块复杂度：示例问题生成迁移到知识库 utils，文件上传统一 100 MB 限制，URL 预处理入库路径与旧 `content_type=url` 行为收敛，并修复 uid、导出 MIME 与异常透传等路由问题。
- 重构智能体配置语义：用户可见的 `AgentConfig` 收敛为数据库持久化的一级 `Agent`，内置 Python Agent 改为智能体后端；新增 `/api/agent` 管理与运行接口，聊天、运行任务、恢复审批和文件预览均从线程绑定的 Agent 解析运行时上下文，前端只提交 `agent_id`，并在模型配置页新增“智能体”管理页签。
- 删除 Upload 与 LightRAG 图谱/知识库能力：知识库类型收敛为 Milvus 与 Dify，只保留 Milvus 知识库内图谱构建/展示/检索，移除独立 `/graph` 页面和默认上传图谱工具。
- 收敛只读知识源连接器：新增 `ReadOnlyConnectors` 基类，Dify 改为声明自身创建参数与校验规则，新增 Notion Data Source 只读知识库并支持 Search/Find/Open；知识库类型接口返回创建参数 schema，前端新建表单按类型动态渲染非 Milvus 配置并统一保存到 `additional_params`。
- 新增知识库 Chunk 持久化：Milvus 知识库索引/更新流程会将 chunks 双写到 PostgreSQL `knowledge_chunks` 表与 Milvus，文件内容查看优先查询 PostgreSQL，并为位置信息、图谱实体关联、标签和抽取结果预留结构化字段；chunk 入库改为分批 embedding 与分批写入，避免大文件一次性写入触发 gRPC 消息大小限制；入库成功后将单文件 chunk 数与 token 数写入文件元数据，并将知识库级总 chunk 与总 token 汇总保存到 metadata，前端文件管理页展示该统计并支持一键修复历史文件缺失的统计值。
- 完善 Milvus 知识库图谱构建：修复 Chunk 图谱写入返回值、Neo4j 同步写入阻塞事件循环、重复构建任务竞态、图谱查询提前终止、Neo4j 连接复用、LLM 抽取超时重试和前端错误详情展示等问题；图谱构建会将 entity/triple 本体与 chunk 引用写入 PostgreSQL，并为唯一 entity/triple 建立 Milvus 语义索引，单文件删除时同步清理图谱引用和孤儿向量。
- 优化图谱抽取器配置：未配置时在图谱中心展示配置入口，抽取方案收敛为 LLM，前端仅保留“更多拓展中”占位；LLM 抽取器使用固定 Prompt + 自定义 Schema，并支持模型参数与并发队列数；已配置后允许修改参数并提示重置重抽风险。修复上传并入库新文件时旧内存 metadata 覆盖数据库图谱配置的问题。
- 新增 Milvus 图谱检索链路：Query 可召回图谱实体和三元组，结合 Chunk 命中实体构造 seed entity，读取 Neo4j 2-hop 子图后用 igraph 执行 PPR，最终以 Chunk 为产物并通过 RRF 与原 Chunk 召回融合；检索配置改为 dataclass 元数据生成，支持 `depend_on` 控制重排序和图检索参数展示。
- 收紧用户管理部门隔离：普通管理员创建用户时固定归属本部门，用户列表、访问选项、详情、更新和删除接口均限制在本部门范围内。
- 修复用户管理列表超过 100 人时被默认分页截断的问题：前端按 `skip/limit` 分批加载用户，并在用户卡片列表中补充分页渲染。
- 调整 Agent 资源默认选择与运行时上下文：未显式配置工具、知识库、MCP、Skills、子智能体时默认启用当前用户可访问/可用的全部资源，显式选择后按允许列表过滤；Agent 创建前统一完成最终资源权限过滤、知识库 `kb_id` 可见范围派生和 Skill prompt/readable 依赖闭包派生，聊天运行时与文件系统预览复用同一结果。
- 重构 Skills 权限与安装流程：Skill 增加 `source_type/share_config/enabled`，内置 Skill 作为启动同步入库的全局资源，不再保留前端安装/更新状态，支持启停但不允许删除；上传和远程添加统一为解析草稿后确认生效范围，安装 slug 优先读取 `SKILL.md` 的 `slug` 字段并保留 `name` 展示名，压缩包名称不参与 slug 校验；管理端支持编辑生效范围与启停；Agent 运行时按当前用户可访问 Skills 派生 prompt/readable 依赖闭包并限制挂载/激活，Skills prompt 改为模型请求级注入以避免污染 runtime context；主智能体恢复 `install_skill` 工具，允许当前用户安装私有 Skill 并激活当前会话，子智能体配置和运行态均禁用该工具。
- 精简历史兼容层：移除 sandbox provisioner `local` 后端别名、ask_user_question 单问题旧协议、JWT 历史默认密钥特殊判断、内置 Skill `SKILLS.md` 文件名回退、运行事件数字 seq 兼容和前端若干旧字段回退。
- 重构知识库共享权限：`share_config` 改为全局共享、部门共享、指定人可访问三档，部门共享必须包含当前用户部门，指定人可访问必须包含当前用户，并补充权限过滤测试。
- 移除知识库沙盒文件系统映射：不再通过 `/home/gem/kbs` 暴露知识库文件树，Agent 继续使用 `query_kb` 与 `open_kb_document` 访问知识库内容。
- 修复 MinerU 文档解析配置说明：文档处理指南原先指引启动 `openai-server`（30000 端口，仅提供 `/v1/chat/completions`），与解析器实际调用的 `/file_parse` 接口不匹配导致 `mineru_ocr` 不可用；更正为使用项目内置的 `mineru-api` 服务（30001 端口），并补充镜像构建与显存调优说明。
- 规范 Agent 知识库 Search/Find/Open 工具协议：`resource_id` 统一表示知识库 `kb_id`，Search 返回结构化 `resource_id/file_id/chunk` 结果，新增 `find_kb_document` 在已知文件内做关键词或正则定位，Open 默认窗口扩大到 1800 行。
- 收敛知识库分块配置：分块预设仅表达策略选择，通用分块参数统一通过 `chunk_parser_config` 传递；移除 `chunk_size`、`chunk_overlap`、`qa_separator` 等旧 root 字段兼容。
- 收敛知识库文件解析参数：文件级 `processing_params` 统一保存 `ocr_engine` 与 `ocr_engine_config`，解析阶段直接使用该结构并保留分块参数快照。
- 修复知识库文件大小显示为 0 的问题：文件上传时 `file_sizes` 参数未正确传播或历史数据缺失导致 DB 中 `file_size` 为 `None`；新增 `MinIOClient.stat_file/astat_file` 获取文件大小方法，`add_file_record` 在 `size` 缺失时从 MinIO 回补，`_load_metadata` 加载元数据后自动为缺少 `size` 的文件从 MinIO 补全并持久化。
- 优化评估基准自动生成：生成任务支持配置队列并发数，默认 10，范围 1-20。
- 完善模型供应商类型：普通聊天模型运行时新增 Anthropic provider type 适配，并清理不再支持的旧 provider type 入口。
- 重梳理知识库评估存储：评估数据集、题目、评估运行和逐题结果统一入库，JSONL 仅作为导入/导出格式；后端和前端 API 统一使用 dataset/run 语义；评估运行支持用户命名，历史记录按名称展示，综合评分只聚合检索指标。
- 扩展知识库上传来源：添加“从工作区上传”模式，后端将当前用户工作区文件预处理上传到 MinIO，前端沿用现有 `addDocuments` 入库链路提交 MinIO URL、内容哈希和文件大小。
- 重构知识库详情页布局：`DatabaseInfo` 改为顶部详情 header + 左侧功能 tab 侧边栏 + 右侧内容区，Milvus 默认进入文件管理，并将检索测试、知识图谱、知识导图、检索配置、RAG 评估和评估基准统一纳入侧边栏导航；只读连接器保留检索测试与检索配置。
- 整合知识导图接口：移除独立 mindmap router 与前端 API 模块，思维导图生成、查询和文件列表接口统一收敛到知识库 API 下。
- 收敛独立模型配置模块运行时：运行时 chat / embedding / rerank 均统一从 provider 模块与模型缓存读取 `provider_id:model_id`；旧版静态模型配置、v1 slash spec、旧模型列表接口和 Ollama 适配已移除；内置 provider 模板补充 XiaomiMiMo、XiaomiMiMo Token Plan CN 与 Kimi Code（`kimi-for-coding`）。
- 调整智能体模型配置默认值：`BaseContext.model` 默认保持为空，运行时按“请求模型 > 智能体配置模型 > 系统默认模型”解析；子智能体未配置模型时继承主智能体当前运行模型，避免把系统默认模型固化进每个智能体配置。
- 调整智能体配置归属与字段权限：`AgentConfig` 从部门共享改为按 `uid` 隔离，所有登录用户可管理自己的配置；`BaseContext` 支持字段级 `auth` 元数据，后端按用户角色过滤可见与可保存的配置项。
- 新增用户级沙盒环境变量：增加 `agent_envs` 表与 `/api/user/agent-env` 接口，设置面板支持当前用户维护 Agent 沙盒环境变量；创建新沙盒时与全局 `sandbox.env` 合并注入，用户变量优先。
- 收敛用户身份命名：原业务登录标识统一改为 `uid`，Agent/LangGraph runtime、conversation、agent_run、sandbox 路径和前端用户态均使用字符串 `uid`；`user_id` 仅保留给外部响应中的数值 `users.id` 或真实外键场景。
- 工作区知识库分类显示：知识库侧边栏按创建者分组为“我的知识库”和“共享知识库”，自己创建的知识库显示在“我的知识库”下，非自己创建的显示在“共享知识库”下；`knowledge_bases` 表新增 `created_by` 字段记录创建者 uid。
- 工作区文件上传支持多选：`/workspace/upload` 与 Viewer 工作区上传统一使用 `files` 多文件字段，一次最多上传 50 个文件，批量上传失败时清理本次已写入文件。
- 聊天附件新增 MinIO tmp 临时上传、可选 PDF/图片解析、确认后加入线程附件的流程；前端改为弹窗内上传、解析与确认。
- 修复智能体对话上传透明 PNG 后图片失真的问题：多模态图片处理在导出 RGB 前会先按白底合成 alpha 通道，避免透明像素中的隐藏颜色被直接转为可见像素；交付物预览优先按文件头识别 MIME，避免 `.jpg` 文件名包裹 PNG 内容时前端按错误格式加载；Agent run 输入消息会持久化为 `multimodal_image`，刷新历史后仍能显示用户上传图片。
- 优化智能体对话页细节：状态面板隐藏空 section，待办名称限制为 20 个中文汉字以内，模型选择器展示供应商名称，并收紧附件状态标签与文件编辑浮动操作样式；
- 标准化 Agent run/SSE 执行链路：run 创建时持久化输入消息并提交后入队，worker 统一写入 Redis Stream envelope，SSE 输出 `event/data/id`、心跳注释、`Last-Event-ID` 回放和终止 `end` 事件；前端强制使用 run API 并支持 ask_user_question 中断后以 resume run 恢复；事件 envelope 构造收敛到统一 helper，前端优先使用 envelope 一级 `thread_id` 路由。
- Agent run SSE 新增 `verbose=false` 精简模式：默认仍返回完整事件载荷；精简模式仅在 SSE 输出前重建最小 payload，跳过 `metadata` 和空 `yuxi.agent_state`，将同一 data 内的 `request_id` 外提为单个字段，移除 chunk 中重复的 `meta`、`metadata`、`thread_id`、`response`、空 `namespace` 和图片 base64 等调试字段，保留消息增量、工具调用、工具结果、非空 Agent state、终止状态和 SSE 游标，前端订阅默认使用精简模式。
- 修复 SiliconFlow MiniMax 与阿里云百炼工具调用流式兼容：二者的 OpenAI 兼容流经 LangGraph v3 event stream 累积工具调用时会丢失关键字段（MiniMax 在参数增量 chunk 返回空 `function.name`，百炼丢失 `tool_call.id`），空值被写入 checkpoint 后会导致工具执行失败或工具结果无法按 `tool_call_id` 关联、工具状态永远停留在“进行中”；这两类提供商默认对工具调用禁用流式模型响应（正文回答仍流式），保留 LangGraph v3 运行事件并拿到完整 tool_call。该缺陷属 LangChain v3 流式协议上游问题（参见 langchain#37420、langchainjs#10937、langgraphjs#2496），截至 langchain-core 1.4.4 仍未修复，待上游修复后可移除对应提供商的禁流式处理。
- 收敛后端模块边界：文档解析从 `plugins.parser` 移动到 `knowledge.parser`，内容审查从 `plugins.guard` 移动到 `services.guard`。
- 收敛文件服务边界：文件预览判断抽为独立服务，Viewer 文件系统的 workspace 分支复用用户 workspace 服务，线程运行时上下文解析从泛化 `filesystem_service` 拆出为 agent runtime helper。
- 升级 DeepAgents 到 0.6.7 并适配新版文件系统协议：SubAgentMiddleware 改为显式 subagent spec，Skills prompt 补齐新版占位符；sandbox/skills backend 复用新版 `ReadResult`、`GlobResult`、`GrepResult` 等协议类型，文件权限在 backend 层明确区分 skills、uploads、outputs 与 workspace，保留最小 `CustomCompositeBackend` 以避免非 route glob 误扫其他 route；Agent 上下文压缩改为复用 DeepAgents SummarizationMiddleware，历史摘要与大工具结果统一 offload 到 outputs。
- 优化聊天输入 @ 文件提及：未创建 Thread 时可搜索用户 workspace，创建 Thread 后按当前对话文件优先、workspace 兜底的来源顺序搜索，并拆分 workspace/thread 缓存避免假 thread 与跨用户缓存污染；输入框与用户消息支持将 raw mention 渲染为带类型图标的引用单元，文件仅显示文件名且保留原始沙盒路径文本。
- 重构子智能体为 Agent-backed 形态：移除旧 `subagents` 表与 `/api/system/subagents` 管理链路，子智能体改为 `agents.is_subagent=true` 且使用 `SubAgentBackend`，创建/编辑统一走 Agent 管理入口；内置后端收敛为 `ChatbotAgent` 与 `SubAgentBackend`，Context 分为 `BaseContext`、`ChatBotContext` 与 `SubAgentContext`；主 Agent 通过 Yuxi task middleware 启动真实子 Agent graph，子智能体不再嵌套调用子智能体。沙盒挂载同步拆分为 child checkpoint thread、父对话 uploads/outputs、用户级 workspace 与子 Agent skills scope；主线程状态记录 `subagent_runs` 并在前端 task 工具中展示子智能体名称、执行状态、child thread 和产物，task 工具结果会暴露 child thread ID 且支持传回 `thread_id` 继续既有子智能体线程；子智能体执行复用 `agent_runs(run_type=subagent)` 记录父 run、child thread 与状态，child thread state 查询以 `agent_runs` 关系为准，不再解析 thread ID 反推父线程；真实流式 E2E 覆盖子智能体输出文件可由父线程文件/Viewer API 读取。流式链路参考 DeepAgents event streaming，后端将 LangGraph v3 raw event 归一化为 Yuxi semantic stream event，按父/子线程归属隔离 run SSE chunk，并支持通过 child thread state 拉取子智能体中间过程。
- 修正评估综合得分计算：`overall_score` 改为有答案准确率时取各题准确率平均，否则取各题 `recall@10` 平均，不再把 recall/f1/各 k 检索指标混合平均；历史已存运行不回填。
- 清理无效鉴权中间件：移除启动时未实际校验令牌的 `AuthMiddleware` 和公开路径残留判断，后端认证边界明确收敛到路由依赖；`/api/auth/me` 改为强制登录并补充未登录访问返回 401 的集成测试。

## v0.6.2 (2026-05-22)

### 新增

- 新增个人工作区预览与管理：提供独立于对话 thread 的用户级 workspace API，并增加“工作区”页面，用于浏览、预览、编辑、上传、下载、删除个人 workspace 文件；默认创建 `agents/AGENTS.md`，并在 Agent 执行时将其内容追加到系统提示词。
- 新增独立模型配置模块：增加 `model_providers` 表、独立管理接口和“模型配置”页面，支持 provider 基础信息、远端候选模型、enabled models 配置和手动添加模型能力。
- 新增远程 Skill 批量安装能力：后端新增 `install_remote_skills_batch()` 与 `POST /remote/install-batch`，前端补充批处理安装 API 和 UI 逻辑。

### 优化

- 下放扩展管理权限：普通管理员现在可进入扩展管理并完整管理 Tools、MCP、SubAgent、Skills；同步放开 Skill 管理接口权限并补充权限测试。
- 调整 Agent 知识库默认选择：未显式配置知识库时默认启用当前用户可访问的全部知识库，显式保存空列表仍表示不启用知识库。
- 优化评估基准自动生成：仅支持 commonrag/Milvus 知识库，默认参考 chunks 数量改为 1；多 chunk 场景复用知识库向量检索选择相似 chunks，不再对全量 chunks 重新计算 embedding。
- 优化 Agent 输入框文件 mention：用户级 workspace 文件候选改为从独立 workspace API 递归加载，不再依赖 active thread；插入时仍转换为 `/home/gem/user-data/workspace/` 沙盒虚拟路径。
- 调整知识库思维导图后端结构：将思维导图路由文件重命名为知识库语义更明确的 router，并把文件列表整理、提示词构建、AI JSON 解析等纯逻辑下沉到知识库 utils。
- 收敛知识库评估后端结构：将评估指标、单题评估、答案生成提示词和自动基准生成算法下沉到 `knowledge/eval`，`EvaluationService` 保留任务、文件和持久化编排职责。
- 扩展管理界面交互逻辑重构：MCP / Subagents / Skills 从“左侧边栏 + 右侧详情面板”调整为“卡片式网格布局 + 路由跳转二级页面”，工具标签页改为卡片网格布局 + 弹窗详情。
- 统一卡片样式：`ExtensionCard` 新增 `tags` prop 并复用于知识库列表页，知识库列表改用 `ExtensionCard` + `ExtensionCardGrid` 替代原有自定义卡片。
- 调整应用主导航：`AppLayout` 升级为默认展开的侧边栏，保留折叠态图标导航，并统一导航项、任务中心、GitHub、用户信息的图标与文字对齐。
- 合并智能体对话导航：移除 `AgentChatComponent` 内部聊天侧边栏，将新建对话入口和对话历史移动到 `AppLayout` 主侧边栏，并通过共享线程 store 统一管理。
- 统一前端 Markdown 预览渲染：新增共享 `MarkdownPreview` 组件与 `markdown_preview` 渲染工具，替换 Agent 消息、文件预览、知识库 chunk、任务工具结果、聊天导出等场景中的旧预览实现。

### 修复

- 修复聊天中普通用户 `@` 提及出不来技能和 MCP 列表的问题：放宽技能列表与 MCP 服务器列表读取接口至已登录用户，并对普通用户请求的 MCP 列表进行敏感连接参数脱敏。
- 修复知识库文档入库状态回退：当已解析文件缺失 `markdown_file` 解析产物时，索引流程会将文件状态恢复为未解析，便于重新解析。
- 修复附件上传后未立即刷新 mention 候选的问题。
- 加固 JWT 鉴权安全：移除历史默认密钥回退，初始化脚本支持生成并持久化 `JWT_SECRET_KEY` 与 `YUXI_INSTANCE_ID`，签发和验证令牌时校验 `iss/aud`，并拒绝已删除或登录锁定用户继续使用旧令牌访问系统。
- 修复模型配置路由请求模型未接收 `embedding_base_url` / `rerank_base_url` 导致前端已填写仍被后端校验拦截的问题。
- 修复知识库文档处理任务状态不一致问题：文件解析失败时任务中心正确显示"失败"而非"已完成"。

## v0.6.1 (2026-04-24)

### 新增

- 合并知识库导航入口：左侧导航仅保留"知识库"，文档知识库与图知识库在页面 header 中通过同一组轻量切换入口切换
- 抽象页面轻量切换 header：知识库与扩展管理页直接共用 `ViewSwitchHeader`，收敛文档知识库、知识图谱、Tools、MCP、Subagents、Skills 等入口的信息层级
- 调整任务中心交互：入口移动到 GitHub 按钮下方，并将右侧抽屉展示改为居中弹窗
- 将 `yuxi` 从 uv workspace 成员调整为 `backend/package` 下可独立构建的本地 Python 包，backend 通过 path dependency 以已安装包形式发现依赖
- 新增 Skills 远程安装能力：Skills 管理页支持填写 `owner/repo` 或 GitHub URL，后端通过隔离的临时 `HOME` 调用 `npx skills add` 下载指定 skill
- 调整部门删除语义：删除部门时不再要求用户数为 0，而是将部门下用户迁移到默认部门
- 扩展 viewer 工作区文件操作：`/home/gem/user-data/workspace` 支持从文件系统面板新建文件夹和上传文件
- 为历史线程补充前端本地配置变更提示：当已有历史消息的对话中切换 Agent、切换配置或编辑配置项时，插入非持久化的信息提示
- 调整 Worker run 模式下的消息首屏反馈：前端发送消息时先乐观渲染用户消息，再将前端生成的 `request_id` 透传给 `/api/chat/runs` 与服务端 `init` 对账
- 调整聊天首页的智能体切换入口：当智能体数量 `>= 4` 或内容区宽度小于 `380px` 时自动收敛为"当前智能体 + 下拉按钮"形式
- 调整智能体对话中的工具调用展示：连续工具调用默认折叠为"调用了 N 个工具"的轻量摘要
- 调整输入框配置入口与侧边栏头尾交互：输入区配置按钮改为轻量 dropdown 触发器

### 修复

- 修复 `incoming-document` Skill 读取来文原文时可能丢失运行时身份的问题：原文落盘现在与沙箱后端一致，从服务端注入的 runtime config、context 或 state 解析 `uid` 与 `thread_id`；原文读取异常会作为工具失败持久化，chat-iframe 同步保留流式工具结果的失败状态，避免工具卡片误显示为成功。
- 修复沙盒 `workspace` 隔离粒度：宿主机目录从共享 `saves/threads/shared/workspace` 收敛为用户级 `saves/threads/shared/<user_id>/workspace`
- 收紧文件系统安全边界：viewer/chat 下载与删除路径统一基于解析后的真实路径做允许目录校验，阻止通过软链接逃逸工作区/线程目录
- 修复 OIDC 原始用户名绑定中的占位用户解析：解析目标用户 ID 时改为从右侧拆分，避免 `sub` 中包含冒号时把已绑定账号误判成冲突账号
- 修复 DOCX 解析中的图片回插顺序：Docling 导出的多个 `<!-- image -->` 占位符现在按文档图片顺序替换
- 修复前端依赖安全告警：通过 `pnpm.overrides` 将传递依赖 `flatted` 锁定到 `3.4.2`、`lodash-es` 锁定到 `4.18.1`
- 修复对话摘要中间件的工具结果卸载链路：摘要触发时改为将大体积 `ToolMessage` 写入当前 agent 可见的 sandbox outputs 路径
- 修复 agents 页对话侧边栏在 `keep-alive` 路由切换后的误关闭问题
- 调整 Milvus 混合检索实现：集合 schema 增加 BM25 稀疏向量字段、BM25 函数和中文 analyzer 配置
- 重构 MCP 运行时配置加载模型：移除 `MCP_SERVERS` 作为运行正确性前提的设计，改为每次直接从数据库读取最新 MCP 配置
- 为知识库检索工具补充 `metadata.filepath` 注入：在 `query_kb` 统一出口基于会话可见知识库构建 `file_id -> /home/gem/kbs/...` 映射并回填 Milvus 检索结果
- 移除知识库沙盒文件系统映射：Agent 不再通过 `/home/gem/kbs` 遍历知识库文件，继续通过 `query_kb` 和 `open_kb_document` 检索与打开文档。

## v0.6.0 (2026-04-01)


### 新增
- 重构后端代码 src -> backend/package/yuxi
- 重构文档解析，统一文档解析体验，并新增 Parser 类
- 新增 LITE 模式启动，启动时不加载知识库、知识图谱相关模块，可以使用 make up-lite 快捷启动
- 新增沙盒环境，详见后续文档更新，统一沙盒虚拟路径前缀默认值为 `/home/gem/user-data`
- 新增基于沙盒的文件系统，前端工作台可以查看文件系统，支持预览（文本、图片、PDF、HTML）、下载文件
- 新增 `present_artifacts` 内置工具：Agent 可将 `/home/gem/user-data/outputs/` 下的结果文件显式写入 LangGraph state 的 `artifacts` 字段，前端支持在输入框顶部以默认折叠的堆叠卡片展示本轮交付物文件，并保持可下载、可预览能力
- 交付物卡片新增“保存到工作区”能力：支持将单个交付物复制到共享目录 `workspace/saved_artifacts/`，并复用现有文件树/预览/mention 体系立即可见
- 新增基于沙盒的知识库只读映射，按“用户可访问知识库 ∩ 当前 Agent 已启用知识库”暴露原始文件与解析后的 Markdown
- 重构附件系统，直接集成在了沙盒文件系统中，附件上传后直接落盘到沙盒挂载目录
- 优化前端流式消息体验：新增通用 `useStreamSmoother` 调度层，统一平滑 Agent runs SSE、普通聊天流与审批恢复流中的 `loading` chunk
- 优化项目文档说明，并添加贡献指南
- 重构前端 Agent 路由结构，体验更加顺畅，切换更加自然（类 chatgpt 体验）
- 新增 API Key 认证功能，支持外部系统通过 API Key 调用系统服务
- 新增 subagents 的支持，支持在 web 中添加 subagents，以及两个内置的子智能体
- 新增内置Skills reporter，并移除内置 Agent reporter，数据库报表将由 Skills 完成
- 新增内置 Skills `deep-reporter`，用于指导生成科研报告、行业调研和其他深度分析类长报告
- 重构内置 Skills/MCP/Subagents 安装/添加/移除机制：内置 skill 支持按需安装、基于 `version + content_hash` 的更新提示与覆盖确认，不再使用服务器级开关切换
- 新增知识库 PDF、图片的预览功能
- 重构后端测试目录结构：按 `unit / integration / e2e` 分层迁移现有测试，拆分全局 `conftest.py`，统一测试入口为 `uv run --group test pytest`，并新增独立测试规范文档 `docs/develop-guides/testing-guidelines.md`
- 新增工具元数据 `config_guide` 字段：后端工具列表接口现在可返回“给人看的配置说明”，前端工具详情页会展示该说明，用于提示工具使用前需要配置的环境变量或入口；首批为 MySQL 工具和 `Qwen-Image` 补充了配置指引
- 补充 Langfuse 集成方案文档：明确采用“云端优先、先 tracing 后 feedback”的接入路径，并约定 Yuxi 的 `user/thread` 到 Langfuse `user_id/session_id` 的映射关系
- 新增面向用户的 Langfuse 集成文档：在“高级配置”分组中说明 Langfuse 的定位、能力、配置方式与查看路径，并与当前 `LANGFUSE_BASE_URL` 配置保持一致

- 新增 chat-iframe 外部用户换票与业务会话隔离：支持可信后端 `/api/external-users/token` 与低信任 iframe `/api/chat-iframe/token` 两种模式，自动创建默认普通用户，并通过 `conversation_scope_key` 隔离同一外部用户在不同业务界面的会话列表。
- 优化 `chat-iframe` 高频聊天体验：模型选择改为按会话草稿隔离，并在打开历史会话时从最近用户消息的 `model_spec` 恢复，避免切换会话后误用其他会话的模型；首轮问答成功后异步调用 `/api/chat/call` 生成会话标题，服务端以受限 `meta.use_fast_model` 标记选择快速模型，人工重命名或标题生成失败均不会影响正式聊天。
- 优化 `chat-iframe` 会话列表与最终回答信息：置顶会话不再侵占普通会话分页额度，前端支持继续加载且跨页去重；最终回答操作区显示模型名称，并从本轮 `query_kb` / Tavily 搜索工具结果汇总知识库分块和网页来源，过程工具卡维持原有展示。
- 完善 `chat-iframe` 上传与消息操作：普通附件在选择、调用和后端确认阶段统一限制为单个 5 MB、一次最多 10 个，图片限制为指定格式且不超过 10 MB；上传失败会回填未成功的草稿。用户消息支持复制，图片支持全屏预览与 ESC 关闭，附件消息展示图标、大小和上传状态。
- 增强 `chat-iframe` Markdown：补齐 Java、SQL、YAML、Go、C/C++、C#、Dockerfile 等常用语言高亮与别名，代码高亮自动跟随系统深浅色偏好；`svg` 围栏经浏览器 DOM 白名单清洗后以图片展示，避免模型输出直接进入 iframe HTML 上下文，未新增 Mermaid 或共享依赖。
- 建立 `chat-iframe` P1 核心体验本地门禁：新增 `test:p1` 顺序执行全量回归、类型检查、lint 和生产构建；补消息复制、图片预览与附件状态组件约束测试。真实浏览器 E2E 仍明确要求在内网部署机配合真实父页面和独立测试身份执行。

<!-- 添加到这里 -->
- 修复 chat-iframe 将 SVG 交付物遗漏在图片预览类型之外的问题，流程图与思维导图现在可使用既有预览入口。
- 修复本地 Graphviz 流程图渲染产物携带 SVG DTD，导致统一 SVG 安全校验误拒绝的问题。
- 修复本地小模型偶发漏选可视化子 Skill 的问题：前置元数据现在明确区分类型未定的总路由与数据图表、流程图、思维导图的显式触发词，并禁止改用文档生成工具或手写 SVG。
- 修复本地小模型在 iframe 页面或附件摘要已直接包含答案时仍错误委派子智能体、重复调用检索工具的问题。

### 修复

- 修复运行时提示词硬编码 Skill 或工具使用方式的问题：chat-iframe 的来文上下文和文档摘要均按 `incomingId` 聚合，不同来文独立分块，同一来文仅展示用户选中的附件并共用分类和元数据；主附件展示摘要与结构化结果，副附件仅展示名称与摘要；技能说明、按需激活与工具开放统一由 Agent 的 SkillsMiddleware 和对应 `SKILL.md` 处理，避免 Agent 未配置能力时收到不可执行的指令。
- 优化 chat-iframe 来文上下文 token：来文级元数据与 `incoming_id` 仅输出一次，移除与已选附件重复的附件清单；结构化事项使用后端中文 schema/字段标签，过滤空值、原文片段和无效“全文”位置，仅在跨附件或有精确位置时保留来源定位。
- 优化 chat-iframe 结构化事项提示词：同一 schema 的标题仅输出一次，事项改为数字编号并使用中文冒号分隔字段和值；主附件身份由后端匹配结果传入，结构化信息不再重复附件名称和 `source_file_id`。
- 重构 chat-iframe 上下文提示词骨架：使用一个完整的 Jinja 模板统一描述网页、来文、附件、结构化事项及多来文循环，代码只准备动态变量并统一渲染；保留现有全局截断规则，便于直接审阅最终提示词结构。
- 优化 chat-iframe 结构化事项表达：仅按 `item_type` 和后端已有的 schema/字段显示标签动态分组；每组逐条拼接非空、非 `source_quote`、非“未明确”的字段，不依赖具体分类、item 名称或新增 schema 配置。
- 补充 chat-iframe 附件结构化内容标题：在各类型结构化事项前统一标识“附件结构化提取结果”，明确其为附件抽取产物并形成清晰层级。
- 前端 Docker 基础镜像不再默认绑定特定镜像代理：Compose 与 Dockerfile 默认使用 Docker Hub，`NODE_ALPINE_IMAGE`、`NGINX_ALPINE_IMAGE` 继续由每台部署机器的 `.env` 或 `.env.prod` 覆盖；开发初始化不再预拉取这两个固定名称的镜像，避免初始化与实际构建使用不同镜像源。
- 调整聊天首页的智能体切换入口：在无历史对话时，智能体数量 `<= 3` 且 `chat-main` 宽度不小于 `380px` 时继续使用横向 segmented；当智能体数量 `>= 4` 或内容区宽度小于 `380px` 时自动收敛为“当前智能体 + 下拉按钮”形式，避免多智能体或窄屏场景下入口被截断
- 发布前一致性修复：统一 0.6.0 版本号（backend/package/web）、更新 dev/prod 镜像标签语义（`0.6.0.dev` / `0.6.0`），并为 `/api/system/health` 补充 `version` 字段，提升部署可观测性与发版追溯能力
- 收敛“状态工作台”自动弹出规则：前端不再因为共享 `workspace` 或文件系统天然存在内容而默认展开，改为仅在 `/home/gem/user-data/uploads` 或 `/home/gem/user-data/outputs` 下检测到实际文件时自动弹出；手动打开、关闭、刷新和伸缩交互保持不变
- 调整智能体 todo 展示语义：待办状态不再作为 `capabilities` 前端开关，而是直接根据运行态 `agent_state.todos` 渲染；同时将 todo 入口从 Agent Panel 移到输入框内的轻量浮层，并让右侧“状态工作台”收敛为文件系统视图，输入框按钮文案同步由“状态”调整为“文件”
- 优化 Agent 输入框 mention 行为：在保留附件 mention 的同时，将共享 `workspace` 文件纳入候选范围；并将 `@` 空查询时的候选列表改为空，仅在继续输入后再执行筛选，避免工作区文件过多时直接铺满下拉面板
- 为前端工作台文件树补齐文件删除能力：`/api/viewer/filesystem/file` 新增删除接口，`AgentPanel` 文件节点新增删除按钮与确认交互，删除后会同步刷新树与预览状态
- 扩展 Agent Panel 状态工作台删除能力：继续复用 `DELETE /api/viewer/filesystem/file`，在保持接口不变的前提下支持删除文件夹；空目录与非空目录现在都会递归删除，`workspace` 下目录也可直接清理，前端目录节点同步新增删除入口与对应确认文案
- 调整前端工作台文件预览交互：恢复默认侧边/弹窗预览，并新增显式“全屏预览”入口；全屏模式下由预览内容直接覆盖整页，仅保留右上角悬浮关闭按钮；同时修复 HTML 文件首次在弹窗中预览偶现白屏的问题，改为在内容更新后强制重建 `iframe`
- 统一 Agent Panel 文件预览与消息区交付物预览组件：两处改为复用同一套 `AgentFilePreview` 预览实现，并为交付物预览补齐与工作台一致的“全屏预览”入口
- 修复交付物卡片展开后的长列表展示：当单轮交付物文件超过面板可见高度时，卡片内容区改为显示纵向滚动条，避免超过约 10 项后底部文件与操作按钮被裁切
- 兼容旧版已安装的内置 `reporter` 技能记录：`update_builtin_skill` 现在会识别由 `system` 或 `builtin-system` 管理的历史记录，避免更新时误报“技能 `reporter` 不是内置 skill”
- 调整沙盒 user-data 目录隔离策略：`workspace` 改为共享目录 `saves/threads/shared/workspace`，`uploads/outputs` 继续保持 thread 级隔离；同时更新 thread artifact 权限校验、viewer 文件系统列举逻辑，以及对应的 router/E2E 测试
- 重构聊天接口请求模型：流式与非流式聊天统一使用 `query + agent_config_id` 请求体，并移除路径中的 `agent_id`；同时修复非流式接口实际误走流式执行链路的问题，改为调用 `invoke_messages` 一次性执行，并补充对应测试
- 修复对话线程与 Agent 配置错位的问题：发送消息时将当前 `agent_config_id` 绑定到 thread 的 `extra_metadata`，线程列表接口返回该绑定值，前端切换历史 thread 时会自动恢复对应配置
- 为沙盒与 viewer 文件系统补齐知识库只读映射：新增 `/home/gem/kbs` 命名空间，按“用户可访问知识库 ∩ 当前 Agent 已启用知识库”暴露原始文件与解析后的 Markdown，并补充对应后端与 viewer 路由测试
- 优化 viewer 文件系统目录树加载：根目录与 `/home/gem/user-data` 改为直接读取本地线程挂载目录，不再为只读树视图触发 sandbox 冷启动，并补充对应后端测试
- 修复 `/home/gem/user-data` 根目录文件不可见的问题：根目录现在会同时展示 thread 目录下的真实文件和 `workspace` 入口，不再只保留固定命名空间目录
- 修复前端工具图标与渲染匹配不准确的问题：工具管理列表与工具调用结果统一改为基于工具 `id` 的精确映射，避免模糊匹配导致的误渲染，未命中的工具不再显示默认扳手图标
- 修复 GitHub Pages 文档部署工作流失败：移除 `actions/setup-node@v4` 对不存在 `docs/package-lock.json` 的缓存依赖，并将 `docs` 目录安装命令从 `npm ci` 调整为 `npm install`，避免因未提交锁文件导致 CI 在依赖缓存和安装阶段直接失败
- 修正沙盒 provisioner backend 命名与配置说明：统一对外使用 `docker` / `kubernetes`，保留 `local` 作为兼容别名；同步清理 compose 中未生效的 provisioner 环境变量、补齐 K8s 相关变量注释，并更新沙盒架构文档中的默认模式与 backend 描述
- 修复智能体配置列表接口在“无配置自动创建默认配置”路径下的参数缺失：补齐 `get_or_create_default` 的 `agent_id` 入参，避免 `/api/chat/agent/{agent_id}/configs` 返回 500
- 修复 LightRAG 同库写入并发导致的入库失败：为 `index_file` / `update_content` 增加按知识库维度的串行锁，并补齐 `documents` 接口 `auto_index` 阶段对最新解析状态的回写与回归测试，避免长时间入库任务进行中再次选择同库文件时直接并发写入报错

<!-- 添加到这里 -->


---


## v0.5

### 新增

- 优化 OCR 体验并新增对 Deepseek OCR 的支持
- 优化 RAG 检索，支持根据文件 pattern 来检索（Agentic Mode）
- 重构智能体对于“工具变更/模型变更”的处理逻辑，无需导入更复杂的中间件
- 重构知识库的 Agentic 配置逻辑，与 Tools 解耦
- 将工具与知识库解耦，在 context 中就完成解耦，虽然最终都是在 Agent 中的 get_tools 中获取
- 优化chunk逻辑，移除 QA 分割，集成到普通分块中，并优化可视化逻辑
- 重构知识库处理逻辑，分为 上传—解析—入库 三个阶段
- 重构 MCP 相关配置，使用数据库来控制 [#469](https://github.com/xerrors/Yuxi/pull/469)
- 使用 docling 解析 office 文件（docx/xlsx/pptx）
- 优化后端的依赖，减少镜像体积 [#428](https://github.com/xerrors/Yuxi/issues/428)
- 优化 liaghtrag 的知识库调用结果，提供 content/graph/both 多个选项
- 优化数据库查询工具，可通过设计环境变量添加描述，让模型更好的调用
- 优化任务组件，改用 postgresql 存储，并新增删除任务的接口
- 支持更多类型的文档源的导入功能（支持后端配置的白名单的 URL 导入）

### 修复

- 修复文件上传弹窗中 OCR 下拉选项展开时不会自动检查服务状态的问题
- 修复知识图谱上传的向量配置错误，并新增模型选择以及 batch size 选择
- 修复部分场景下获取工具列表报错 [#470](https://github.com/xerrors/Yuxi/pull/470)
- 修改方法备注信息 [#478](https://github.com/xerrors/Yuxi/pull/478)
- 修复多次 human-in-the-loop 的渲染解析问题 [#453](https://github.com/xerrors/Yuxi/issues/453) [#475](https://github.com/xerrors/Yuxi/pull/475)
- 修复沙盒后端接入回归：补齐 composite backend 的 `sandbox_backend` 参数、限制 `/api/sandbox/prepare` 仅允许访问当前用户线程、确保 `release()` 之后的 `destroy()` 会真正停止热池容器，并恢复 docker-compose 的完整模式默认值
- 重构沙盒为 deer-flow 风格的 AIO provider：切换为 thread-local sandbox、统一 `/home/gem/user-data/{workspace,uploads,outputs}` 固定路径、移除公开 `/api/sandbox/*` 生命周期接口，并补充 lite 模式下的 provider 生命周期、filesystem API 与 sandbox 复用/隔离 E2E 验证
- 调整聊天附件存储链路：线程附件改为直接落盘到 `saves/threads/<thread_id>/user-data/uploads`，解析成功后额外生成 `uploads/attachments/*.md`，不再依赖 MinIO 或显式上传到 sandbox
- 修复知识库文件列表包体异常膨胀：上传阶段不再把批次级 `content_hashes` 写入每个文件的 `processing_params`，并从数据库详情列表接口中移除该字段，改为按需读取单文件详情

## v0.4

### 新增
- 新增对于上传附件的智能体中间件，详见[文档](https://xerrors.github.io/Yuxi/advanced/agents-config.html#%E6%96%87%E4%BB%B6%E4%B8%8A%E4%BC%A0%E4%B8%AD%E9%97%B4%E4%BB%B6)
- 新增多模态模型支持（当前仅支持图片），详见[文档](https://xerrors.github.io/Yuxi/advanced/agents-config.html#%E5%A4%9A%E6%A8%A1%E6%80%81%E5%9B%BE%E7%89%87%E6%94%AF%E6%8C%81)
- 新建 DeepAgents 智能体（深度分析智能体），支持 todo，files 等渲染，支持文件的下载。
- 新增基于知识库文件生成思维导图功能（[#335](https://github.com/xerrors/Yuxi/pull/335#issuecomment-3530976425)）
- 新增基于知识库文件生成示例问题功能（[#335](https://github.com/xerrors/Yuxi/pull/335#issuecomment-3530976425)）
- 新增知识库支持文件夹/压缩包上传的功能（[#335](https://github.com/xerrors/Yuxi/pull/335#issuecomment-3530976425)）
- 新增自定义模型支持、新增 dashscope rerank/embeddings 模型的支持
- 新增文档解析的图片支持，已支持 MinerU Officical、Docs、Markdown Zip格式
- 新增暗色模式支持并调整整体 UI（[#343](https://github.com/xerrors/Yuxi/pull/343)）
- 新增知识库评估功能，支持导入评估基准或者自动构建评估基准（目前仅支持Milvus类型知识库）详见[文档](https://xerrors.github.io/Yuxi/intro/evaluation.html)
- 新增同名文件处理逻辑：遇到同名文件则在上传区域提示，是否删除旧文件
- 新增生产环境部署脚本，固定 python 依赖版本，提升部署稳定性
- 优化图谱可视化方式，统一图谱数据结构，统一使用基于 G6 的可视化方式，同时支持上传带属性的图谱文件，详见[文档](https://xerrors.github.io/Yuxi/intro/knowledge-base.html#_1-%E4%BB%A5%E4%B8%89%E5%85%83%E7%BB%84%E5%BD%A2%E5%BC%8F%E5%AF%BC%E5%85%A5)
- 优化 DBManager / ConversationManager，支持异步操作
- 优化 知识库详情页面，更加简洁清晰，增强文件下载功能

### 修复
- 修复 GitHub Actions 的 Ruff CI 在仓库根目录执行 `uv sync` 导致找不到 `backend/pyproject.toml` 的问题，同时统一检查路径为 `backend/package`
- 修复重排序模型实际未生效的问题
- 修复消息中断后消息消失的问题，并改善异常效果
- 修复当前版本如果调用结果为空的时候，工具调用状态会一直处于调用状态，尽管调用是成功的
- 修复检索配置实际未生效的问题
- 修复 sandbox 文件系统 `ls` 在异常输出下触发 `KeyError: 'path'` 的问题，并将工具调用异常降级为错误消息，避免直接中断聊天 stream
- 修复智能体状态面板中文件树仍依赖 `agent_state.files` 的问题，改为通过真实 `/api/filesystem/*` 接口按层懒加载后端可见文件系统，并让输入框下方状态按钮常态化打开工作区视图
- 为工作台新增 viewer-oriented filesystem service 与 `/api/viewer/filesystem/*` 接口，解耦 agent backend 语义，支持真实目录浏览、原始文件读取与下载
- 重写沙盒技术文档，明确 thread-local sandbox、viewer-oriented filesystem service、`/mnt` 命名空间、skills 可见性与当前实现边界，替换过时的 `/api/sandbox/*` 与 user-level 设计描述
- 收紧沙盒遗留代码：修复未注册 `sandbox_router` 中残留的 user/thread 参数错位，改进宿主机挂载路径映射逻辑，并为 remote sandbox provisioner 增加基础 URL 校验与销毁失败日志
- 修复 builtin skill 内容哈希计算对单文件使用 `read_bytes()` 的无上限内存读取问题，改为分块计算并补充回归测试

### 破坏性更新

- 移除 Chroma 的支持，当前版本标记为移除
- 移除模型配置预设的 TogetherAI


## v0.3
### Added
- 添加测试脚本，覆盖最常见的功能（已覆盖API）
- 新建 tasker 模块，用来管理所有的后台任务，UI 上使用侧边栏管理。Tasker 中获取历史任务的时候，仅获取 top100 个 task。
- 优化对文档信息的检索展示（检索结果页、详情页）
- 优化全局配置的管理模型，优化配置管理
- 支持 MinerU 2.5 的解析方法 <Badge type="info" text="0.3.5" />
- 修改现有的智能体Demo，并尽量将默认助手的特性兼容到 LangGraph 的 [`create_agent`](https://docs.langchain.com/oss/python/langchain/agents) 中
- 基于 create_agent 创建 SQL Viewer 智能体 <Badge type="info" text="0.3.5" />
- 优化 MCP 逻辑，支持 common + special 创建方式 <Badge type="info" text="0.3.5" />
- LightRAG 知识库应该可以支持修改 LLM

### Fixed
- 修复本地知识库的 metadata 和 向量数据库中不一致的情况。
- v1 版本的 LangGraph 的工具渲染有问题
- upload 接口会阻塞主进程
- LightRAG 知识库查看不了解析后的文本，偶然出现，未复现
- 智能体的加载状态有问题：（1）智能体加载没有动画；（2）切换对话和加载中，使用同一个loading状态。
- 前端工具调用渲染出现问题
- 当前 ReAct 智能体有消息顺序错乱的 bug，且不会默认调用工具
- 修复文件管理：（1）文件选择的时候会跨数据库；（2）文件校验会算上失败的文件；
