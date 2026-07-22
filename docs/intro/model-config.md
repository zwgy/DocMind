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

对话模型的“上下文长度”填写**推理服务实际部署上限**，单位为 Token。例如，部署实例限制为 32K 时填写 `32768`。从远端模型列表添加时，如果页面“上下文”显示为 `-`，说明 `/models` 没有提供可用值，需要在模型配置中手动填写。

不要混淆以下三个值：

| 概念 | 含义 | 是否填写到模型配置 |
|------|------|-------------------|
| 模型架构上限 | 模型文件或厂商说明中的理论最大窗口 | 否，仅作参考 |
| 推理服务部署上限 | 当前 Ollama、GPUStack 或其他推理实例实际接受的窗口 | 是 |
| 应用可安全使用的输入上限 | 系统为工具定义、模型输出预留空间后开始压缩的位置 | 自动计算 |

系统会在管理员设置的绝对摘要阈值与模型部署上限的 70% 中较早处压缩上下文。因此，填写过大的模型理论值会使压缩过晚，仍可能发生上下文溢出。

#### 常见后端的获取方式

- **Ollama**：先运行 `ollama ps` 确认目标模型已经加载；再请求 `GET http://<ollama-host>:11434/api/ps`，填写对应模型的 `context_length`。`ollama show <model>` 显示的是模型架构信息，只能作为上限参考，不能替代当前实例的实际值。
- **GPUStack**：在模型部署的后端参数中读取实际限制。vLLM 使用 `--max-model-len`，SGLang 使用 `--context-length`，MindIE 使用 `--max-seq-len`，llama-box 使用 `--ctx-size`。
- **其他 OpenAI 兼容服务**：`/models` 的 `context_length` 不是标准字段。只有确认该字段代表当前部署实例时才能直接采用；否则应按照服务部署参数手动填写。

模型配置页会保留远端自动获取的值，但管理员可以覆盖它；手工值应始终以当前实例的部署配置为准。

### 移除模型

在供应商详情的已启用模型列表中移除不需要的模型。

## 模型标识格式

运行时模型统一使用 `provider_id:model_id` 格式，例如 `siliconflow-cn:Pro/BAAI/bge-m3`。`model_id` 可以包含 `/`，系统只按第一个 `:` 区分供应商与模型 ID。

旧版 `provider/model`、旧版知识库 JSON 模型字段、配置文件中的 `model_names` / `embed_model_names` / `reranker_names` 不再作为运行时模型来源。历史知识库或 Agent 配置如果仍保存旧格式，需要在界面中重新选择新版模型后保存。

## Ollama 支持

当前版本不再内置 Ollama provider type，也不再提供 Ollama embedding 运行时适配。已有 Ollama embedding 知识库需要管理员选择新的 embedding 模型并重建索引，避免不同向量空间混用。

Ollama 对话模型可以作为 OpenAI 兼容供应商手动配置；请按“上下文长度（Token）”中的方式填写当前实例实际窗口。

## 常见问题

**凭证缺失警告**：检查 API Key 是否正确配置，或确认环境变量是否已设置。

**模型配置未生效**：确认模型已添加至供应商的已启用列表中。
