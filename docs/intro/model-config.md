# 模型配置

## 概述

系统统一通过 **系统设置 → 模型配置** 页面管理所有模型（对话模型、嵌入模型、重排模型），无需修改配置文件。

## 配置路径

```
系统设置 → 模型配置
```

## API 凭证配置

支持两种凭证配置方式：

| 方式 | 适用场景 |
|------|----------|
| 环境变量 | 生产环境或不愿在界面暴露 Key 的场景 |
| 直接填写 | 开发调试，追求配置便利性 |

**环境变量方式**：在供应商配置中填写变量名（如 `SILICONFLOW_API_KEY`），确保运行时环境已配置对应变量。

**直接填写方式**：在供应商配置中直接填入 API Key。

## 供应商管理

### 内置供应商模板

系统启动时会同步一组内置 provider 模板。模板只提供 Provider ID、Base URL、凭证环境变量和远端模型发现地址；实际是否可用仍取决于你是否配置凭证、启用供应商并添加模型。

| 供应商 | Provider ID | 支持类型 | 凭证环境变量 |
|--------|-------------|----------|--------------|
| OpenAI | `openai` | chat | `OPENAI_API_KEY` |
| DeepSeek | `deepseek` | chat | `DEEPSEEK_API_KEY` |
| DashScope | `alibaba` | chat, embedding, rerank | `DASHSCOPE_API_KEY` |
| Aliyun Coding Plan | `alibaba-coding-plan-cn` | chat | `DASHSCOPE_API_KEY` |
| Aliyun Coding Plan International | `alibaba-coding-plan` | chat | `DASHSCOPE_API_KEY` |
| Zhipu BigModel | `zhipuai` | chat | `ZHIPUAI_API_KEY` |
| Zhipu BigModel Coding Plan | `zhipuai-coding-plan` | chat | `ZHIPUAI_API_KEY` |
| Z.AI | `zai` | chat | `ZAI_API_KEY` |
| Z.AI Coding Plan | `zai-coding-plan` | chat | `ZAI_API_KEY` |
| XiaomiMiMo Token Plan | `xiaomi-token-plan-cn` | chat | `XIAOMI_MIMO_TOKEN_PLAN_API_KEY` |
| XiaomiMiMo | `xiaomi` | chat | `XIAOMI_MIMO_API_KEY` |
| Kimi Code | `kimi-for-coding` | chat | `KIMI_CODE_API_KEY` |
| Moonshot | `moonshotai-cn` | chat | `MOONSHOT_API_KEY` |
| Moonshot International | `moonshotai` | chat | `MOONSHOT_API_KEY` |
| MiniMax | `minimax-cn` | chat | `MINIMAX_API_KEY` |
| MiniMax International | `minimax` | chat | `MINIMAX_API_KEY` |
| OpenRouter | `openrouter` | chat, embedding | `OPENROUTER_API_KEY` |
| ModelScope | `modelscope` | chat | `MODELSCOPE_ACCESS_TOKEN` |
| OpenCode | `opencode` | chat | 无默认环境变量 |
| SiliconFlow | `siliconflow-cn` | chat, embedding, rerank | `SILICONFLOW_API_KEY` |
| SiliconFlow International | `siliconflow` | chat, embedding, rerank | `SILICONFLOW_GLOBAL_API_KEY` |

其中 `alibaba`、`siliconflow-cn` 预置了部分 embedding / rerank 模型；其他供应商通常需要进入详情页通过「获取远程模型」或「手动添加」补充模型。

### 操作流程

1. **新增供应商**：点击「新增供应商」，填写基本信息（Provider ID、Base URL 等）
2. **配置凭证**：填写 API Key 或环境变量名
3. **启用供应商**：开启供应商状态开关
4. **获取模型**：进入供应商详情，点击「获取远程模型」从 API 拉取可用模型列表

## 模型管理

### 添加模型

**方式一：从远端拉取**

进入供应商详情 → 点击「获取远程模型」→ 从候选列表中选择添加

**方式二：手动添加**

进入供应商详情 → 点击「手动添加」→ 填写模型 ID 和类型

### 配置参数

嵌入模型（embedding）需配置向量维度，请参考模型提供商的规格说明。

### 上下文长度（Token）

对话模型的“上下文长度”填写**推理服务实际部署上限**，单位为 Token。例如，部署实例限制为 32K 时填写 `32768`。该项可以留空：添加或编辑模型时，系统先按精确模型 ID 检查 OpenAI-compatible `/models` 的常见扩展字段；没有可用值时使用 LangChain 模型资料；两者均无法解析时使用系统默认 `32768`。Embedding、Rerank 不参与 Agent 对话上下文压缩，无需也不会保存此项。

模型列表会同时显示当前生效值及来源：`手动配置`、`模型服务`、`模型资料`、`默认值`。手动配置优先级最高；“默认值”只保证应用有可用预算，并不能证明它等于当前推理实例的真实窗口，看到该来源时应按部署参数核对。

不要混淆以下三个值：

| 概念 | 含义 | 是否填写到模型配置 |
|------|------|-------------------|
| 模型架构上限 | 模型文件或厂商说明中的理论最大窗口 | 否，仅作参考 |
| 推理服务部署上限 | 当前 Ollama、GPUStack 或其他推理实例实际接受的窗口 | 建议填写；留空时自动解析 |
| 应用可安全使用的输入上限 | 系统为工具定义、模型输出预留空间后开始压缩的位置 | 自动计算 |

对话模型可选配置“最低输出预留 Token”：这是系统在压缩输入前必须保留的回答空间，默认使用部署级 `4096`。它只参与输入预算计算，不会作为模型实际输出上限；模型会在剩余上下文空间内自行完成回答。只有调用方显式传入 `max_tokens` 等参数时，才会对该次请求施加真实输出上限。

“上下文安全缓冲 Token”可选。留空时使用部署级默认值；当某个模型服务的消息协议或 Token 计数偏差更大时，可为该模型单独调高。它不是窗口比例，也不表示模型能力。

“模型请求参数 JSON”只适用于对话模型，用于按模型服务协议保存并原样传递请求参数；默认 `{}` 表示跟随服务默认值。它不是通用的“思考模式”开关：OpenAI 兼容服务可能使用 `reasoning_effort`，而 Qwen/vLLM、Gemini 等服务可能使用不同字段或嵌套结构。填写前应以当前服务的官方协议为准；例如需要关闭某个 OpenAI 兼容服务的思考时，可填写 `{"reasoning_effort":"none"}`。参数错误会由模型服务返回，保存配置后应通过实际对话验证首个可见事件和答案质量。

系统不会在模型调用前请求 `/tokenize`。新会话先按最终系统提示词、消息和工具 Schema 做本地保守估算；模型响应带有 usage 时，系统会自动记录本地估算与实际输入的最大正误差和倍率，下一轮据此提前压缩。切换模型、部署地址或工具 Schema 后会自动重新校准，不需要为 Qwen、DeepSeek、Ollama、GPUStack 或 vLLM 额外配置 tokenizer。

因此，支持 usage 的模型从第一次成功响应后可以获得更可靠的后续准入；模型完全不返回 usage 时仍使用本地保守估算。没有模型原生计数的情况下，系统不能承诺全新会话的第一次任意超大请求绝不触及模型窗口，但会在供应商返回明确上下文错误时压缩旧历史并重试一次。

#### 常见后端的获取方式

- **Ollama**：仅用于开发测试时可运行 `ollama ps` 查看已加载实例的上下文长度。本系统不调用 Ollama 专用接口自动探测。
- **GPUStack**：在模型部署的后端参数中读取实际限制。vLLM 使用 `--max-model-len`，SGLang 使用 `--context-length`，MindIE 使用 `--max-seq-len`，llama-box 使用 `--ctx-size`。当前版本不读取 GPUStack Route Meta 或管理 API。
- **OpenAI 兼容服务**：系统会尝试读取 `/models` 返回的 `max_model_len`、`context_length`、`max_context_length`、`max_input_tokens` 和 `top_provider.context_length`。这些都不是 OpenAI 标准模型卡的必填字段；例如 vLLM 可返回 `max_model_len`，GPUStack 标准 `/v1/models` 当前不保证透传该值。无法探测时会继续使用模型资料或默认值。

模型配置页会保留从模型服务自动获取的值，但管理员可以覆盖它；手工值应始终以当前实例的部署配置为准。探测失败不会阻止保存，也不会在对话或切换模型时重复发起探测请求。

### 移除模型

在供应商详情的已启用模型列表中移除不需要的模型。

## 模型标识格式

运行时模型统一使用 `provider_id:model_id` 格式，例如 `siliconflow-cn:Pro/BAAI/bge-m3`。`model_id` 可以包含 `/`，系统只按第一个 `:` 区分供应商与模型 ID。

旧版 `provider/model`、旧版知识库 JSON 模型字段、配置文件中的 `model_names` / `embed_model_names` / `reranker_names` 不再作为运行时模型来源。历史知识库或 Agent 配置如果仍保存旧格式，需要在界面中重新选择新版模型后保存。

## Ollama 支持

当前版本保留 Ollama 对话模型运行时适配，主要用于开发测试；不再提供 Ollama embedding 运行时适配。已有 Ollama embedding 知识库需要管理员选择新的 embedding 模型并重建索引，避免不同向量空间混用。请按“上下文长度（Token）”中的方式核对当前实例实际窗口。

## 常见问题

**凭证缺失警告**：检查 API Key 是否正确配置，或确认环境变量是否已设置。

**模型配置未生效**：确认模型已添加至供应商的已启用列表中。
