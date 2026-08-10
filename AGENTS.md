
# 项目目录结构 (Project Overview)

Yuxi 是一个基于大模型的智能知识库与知识图谱智能体开发平台，融合了 RAG 技术与知识图谱技术，基于 LangGraph v1 + Vue.js + FastAPI + LightRAG 架构构建。项目完全通过 Docker Compose 进行管理；后端支持热重载，前端默认使用稳定的静态构建进行联调。

架构代码地图见 [ARCHITECTURE.md](ARCHITECTURE.md)。修改不熟悉的模块前，先阅读其中的后端、前端、运行链路和架构不变量说明，再用符号搜索定位具体实现；该文档只维护相对稳定的系统边界，不替代细节文档或源码注释。

## 开发准则

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## 架构与设计原则

- 坚持模块化编程：每个模块只承担清晰职责，通过稳定接口协作。
- 遵守开闭原则：优先通过新增实现、策略、适配器或注册机制扩展能力，避免频繁修改已稳定的核心流程。
- 保持函数和类的单一职责。一个函数如果同时处理编排、IO、格式转换和业务判断，应优先拆分。
- 不为尚未出现的场景提前构建复杂框架。
- 不要过度设计，编写代码前先想想有没有现成库、模块、开源项目等，没有的话再自己动手写，保持代码易懂、易读、整洁。

## 代码约定

- 文档、代码和注释优先使用 UTF-8；中文文档保持中文表达，不要引入乱码内容。
- 新增变量、函数、类和关键业务逻辑应使用清晰的中文注释，优先解释职责、约束和设计原因。
- 标识符优先采用业界常见、语义直接的英文命名，避免生僻词、含义不明的缩写和自造术语；同一概念在数据库模型、Schema、服务、API 和前端中保持命名一致。

## 文档同步

- 代码更新必须同步检查文档是否需要更新，尤其是 `README.md`、`docs/` 和本文件。
- 改变安装、启动、测试、部署、依赖、配置、环境变量、目录结构、接口行为、架构边界、数据模型、任务流程、Skill 约束或安全策略时，必须同步更新对应文档。
- `README.md` 面向人类开发者，记录项目介绍、快速开始、常用命令和导航。
- `AGENTS.md` 面向编程智能体，记录稳定、可执行、会影响 Agent 行为的项目规则。

## 开发与调试工作流 (Development & Debugging Workflow)

本项目完全通过 Docker Compose 进行管理。所有开发和调试都应在运行的容器环境中进行。使用 `docker compose up -d` 命令进行构建和启动。

**核心原则**:

1. `api-dev` 和 `worker-dev` 挂载后端源码并支持热重载；`web-dev` 与 `chat-iframe-dev` 默认运行 Vite 构建后的 Nginx 静态资源，避免 HMR 连接抖动刷新业务页面。修改前端后使用 `docker compose up -d --build web chat-iframe` 重建受影响服务。开始调试前先检查 `docker ps` 和相关容器日志，具体定义见 [docker-compose.yml](docker-compose.yml)。
2. 开发完成之后必须进行 检查 -> 测试 -> Lint，以及端到端测试，测试脚本不完善时应完善脚本。
3. 测试规范务必遵守 [testing-guidelines.md](docs/develop-guides/testing-guidelines.md) 中的规范，测试脚本务必放在 backend/test 目录下，并且在提交前确保测试通过。
4. 非常重要！千万不要使用过度的防御/回退机制来掩盖设计上的缺陷，良好的软件应该在预设的条件下运行，其余情况均应该及时发现问题/错误并修复，而不是通过增加冗余代码来掩盖问题。

### Firecrawl 调研工具

- 调研任务确实需要使用 Firecrawl 时，先执行状态检查：Windows PowerShell 使用 `firecrawl.cmd --status`，避免 npm `.ps1` shim 受执行策略干扰；Linux/macOS 使用 `firecrawl --status`。
- Codex 新会话的受限沙箱可能保留用户级包目录的 `PATH`，但拒绝读取或执行其中的文件。此时命令解析或全局包查询返回未找到/空结果属于沙箱假阴性，不能据此判断 Firecrawl 未安装。
- 遇到未找到或拒绝访问时，必须使用 `sandbox_permissions=require_escalated` 在宿主机用户环境重试对应平台的状态命令。若命令名仍无法解析，则通过 `npm config get prefix` 获取全局 npm 前缀（Windows PowerShell 使用 `npm.cmd` 避免 `.ps1` shim 干扰），从该前缀的可执行文件目录中定位并授权运行 Firecrawl。后续 Firecrawl 调研命令沿用同一授权模式。
- 只有宿主机授权检查也确认 CLI 不存在时，才能报告未安装或执行安装流程。不得在沙箱首次检测失败后直接回退到 `npx`，因为沙箱可能同时无法访问 npm 缓存和网络，从而产生误导性的启动超时。

### 需求沟通规范

在沟通需求的时候，当需求不明确的时候，需要主动挖掘需求细节，对齐需求的验收标准，明确需求的优先级和范围，避免模糊需求导致的过度设计和不必要的工作。

- 需求/修改 明确之后，如果改动较大，则需要在 docs/vibe 目录下创建一个包含日期的文档，记录需求的细节和验收标准
- 该需求文档中，还应该包括本次任务的目标以及 checklist（简要）

### 前端开发规范
- 使用 pnpm 管理
- API 接口规范：所有的 API 接口都应该定义在 web/src/apis 下面
- Icon 应该优先从 lucide-vue-next （推荐，但是需要注意尺寸）
- 样式使用 less，非特殊情况必须使用 [base.css](web/src/assets/css/base.css) 中的颜色变量
- UI 设计规范详见 [design](docs/develop-guides/design.md)


### 后端开发规范

```bash
# 代码检查和格式化
make format        # 格式化代码

```
注意：
- Python 代码要符合 pythonic 风格
- 尽量使用较新的语法，避免使用旧版本的语法（版本兼容到 3.12+）
- 更新 [changelog.md](docs/develop-guides/changelog.md) 文档记录本次修改，多个类似的功能更新已经补充在一起
- 开发完成后务必在 docker 中进行测试，可以读取 .env 获取管理员账户和密码
- 不允许把代码写得稀碎：不要为简单线性逻辑拆出一堆细碎 helper；优先写成职责清晰、结构完整、可一眼读懂的实现。
- 拆函数必须服务于明确的复用、隔离副作用或降低认知负担；如果拆分后调用链更绕、上下文更分散，就应合并回更直接的实现。

**其他**：

- 如果需要新建说明文档（仅开发者可见，非必要不创建），则保存在 `docs/vibe` 文件夹下面
- 代码更新后要检查文档部分是否有需要更新的地方，文档的目录定义在 `docs/.vitepress/config.mts` 中
- 如果新增面向用户的正式文档，除了补正文档内容外，还需要同步更新 `docs/.vitepress/config.mts` 的导航；Langfuse 集成说明归档在 `docs/agents` 分组下维护，并同步更新 `docs/develop-guides/changelog.md`

## 提交规范

1. 参考 [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) 规范编写提交信息。
2. 使用中文提交信息，标题简洁明了，描述具体改动内容和原因。
3. 创建 PR 必须参考 [contributing.md](docs/develop-guides/contributing.md) 以及 PR 模板[PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md)，并在提交前完成其中的检查项。

**特别注意**

- 项目使用模型是本地部署模型，如Qwen3.6-27、Qwen3.6-35B-A3B等模型，在涉及框架设计、性能、问答效果等问题时应充分考虑这个限制。
- 新增代码逻辑必须遵守“代码约定”中的中文注释要求。注释优先解释 WHY，例如为什么要做边界隔离、为什么要降级、为什么要人工确认、为什么要限制工具调用；不要只复述代码在做什么。
