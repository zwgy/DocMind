---
name: incoming-document
slug: incoming-document
description: "用户提到来文、收文、文号，或按标题查询已收录来文及其主文件、附件、摘要、原文时必须使用；来文无需进入知识库，不得改用 knowledge-base。"
---

# 来文查询与解读技能

用户询问当前来文、历史来文、分类、业务事项、数量分布或附件原文时使用本技能。用户给出标题或文号并询问主文件、附件、内容或原文时，即使当前工作区没有文件，也必须先用本技能查询系统已收录来文，不要先用 `grep` 或 `glob` 扫描工作区。默认先使用来文级摘要和结构化结果，只有信息不足或需要原文依据时才读取具体附件。

## 术语

- **来文**：一条完整业务记录，由唯一 `incoming_id` 标识，包含来文级摘要、分类、结构化结果和文件清单。
- **文件**：来文中的主文件或附件，每个文件由 `source_file_id` 标识并拥有独立原文。不要把单个文件当作整份来文。

## 首要决策

- 用户要求“查找、列出、有哪些”来文：搜索后直接按工具顺序展示分页候选，不反问。
- 用户要求查看、解读或下载**一份**来文，但没有唯一标识：先搜索。若 `total > 1`，下一步只能调用 `ask_user_question`，不能先回答、展开摘要或用普通文本提问。
- 搜索结果只是候选摘要，不是来文详情。选定唯一 `incoming_id` 后，必须调用 `read_incoming_document(include_full_text=false)` 才能列出具体主文件、附件名称和正式结构化结果。

`ask_user_question` 最小调用示例；实际选项使用搜索结果中的标题、文号和真实 `incoming_id`：

```text
ask_user_question(questions=[{
  "question_id": "incoming_id",
  "question": "请选择要查看的来文",
  "options": [
    {"label": "来文标题一（文号一）", "value": "真实 incoming_id 一"},
    {"label": "来文标题二（文号二）", "value": "真实 incoming_id 二"}
  ],
  "multi_select": false,
  "allow_other": true
}])
```

## 可用工具

- `search_incoming_documents`：按来文日期、主分类、条目类型、标题、文号、发文单位或关键词分页查找来文；通用关键词匹配标题、文号、发文单位、摘要和附件名。`has_main_file` 表示是否有主文件，`attachment_count` 只统计主文件之外的附件。
- `read_incoming_document`：`include_full_text=false` 时读取来文整体结论、附件清单和正式结构化结果；`include_full_text=true` 时将指定文件的 Markdown 写入当前会话目录并返回路径，省略 `source_file_ids` 时默认读取主文件。
- `download_incoming_document_files`：将指定主文件或附件的原始文件写入当前会话 outputs，供用户预览或下载。
- `get_incoming_document_statistics`：按与查询相同的条件统计来文，并按分类、条目类型和月份聚合。
- `ask_user_question`：用户要求查看、下载或解读单篇来文，但搜索结果无法唯一确定目标，或关键范围不明确时，请用户选择。
- `grep`：在已经落盘的 Markdown 原文中按字面关键词定位匹配行；大文件核验时先定位，不要从头盲读大窗口。
- `read_file`：读取 `read_incoming_document` 返回的 `markdown_path`，用于核验附件原文。
- `present_artifacts`：把已写入当前线程 outputs 的 Markdown 文件作为交付物展示给用户。

条目类型参数支持内部 ID 或当前中文名称。工具返回的 `item_type_labels` 是当前有效映射；未知类型不要猜测，应依据工具返回的支持列表修正查询。

## 固定流程

先判断用户任务，只执行对应分支。每次只调用一个工具，检查返回后再继续。

### A. 单篇来文解读

单篇来文的少量条款核验由当前 Agent 按下列步骤直接完成，不调用 `task` 委派子智能体，避免转述时改变条款编号或文件名。

1. 先按“信息充分性”判断能否直接回答，不因页面中存在 `incoming_id`、`source_file_id` 或工具可用就读取原文：
   - 仅问主旨、适用范围、概览、已明确提取的职责或管理要求时，且每个关键结论都能从页面中的摘要或结构化结果直接得到、相关附件均为 `ready` 且摘要未截断，可以直接回答，不调工具。
   - 用户要求原文依据、引用、逐条或完整列举、具体条款/页码/表格/数字、某一附件的细节、核验或比对，或回答所需事实未明确出现在页面摘要和结构化结果中时，必须读取对应原文。
   - 任一相关附件摘要截断、解析未完成、没有摘要或结构化结果，或无法确认当前资料是否覆盖用户问题时，不能以摘要补全猜测；读取原文或说明当前无法核验。
   - 回复前检查每个关键结论的来源范围；只基于摘要和结构化结果回答时，明确这是基于当前已提取信息的概览，不将其表述为逐字原文结论。
2. 已知真实 `incoming_id` 但上述信息不完整时，读取来文级详情：

   ```text
   read_incoming_document(incoming_id="真实 incoming_id", include_full_text=false)
   ```

3. 不知道 `incoming_id` 时，先搜索：

   ```text
   search_incoming_documents(keyword="用户关键词", page=1, page_size=20)
   ```

4. 搜索只命中一份，或用户选定一份后，再使用真实 `incoming_id` 读取详情。单篇操作命中多份时严格遵守“首要决策”，不要自行选择。
5. 用户要求下载原始主文件或附件时，从附件清单选择真实 `source_file_id`，调用：

   ```text
   download_incoming_document_files(
     incoming_id="真实 incoming_id",
     source_file_ids=["附件清单中的 source_file_id"]
   )
   ```

   返回 `original_path` 后调用 `present_artifacts` 交付；不要把 MinIO 地址或宿主机路径返回给用户。
6. 用户要求交付 Markdown 文件，或需要原文依据、具体附件内容、核验细节时，从页面上下文或附件清单选择真实 `source_file_id`。如果页面上下文已经提供真实 `incoming_id` 和 `source_file_id`，直接调用原文模式，不要先重复读取来文详情：
   - 用户明确核验主文件且已经通过搜索获得唯一 `incoming_id` 时，直接调用原文模式并省略 `source_file_ids`，不要先读取包含整套结构化结果的来文详情。
   - 用户核验指定附件时，仍须先从页面上下文或附件清单取得该附件的真实 `source_file_id`。

   ```text
   read_incoming_document(
     incoming_id="真实 incoming_id",
     source_file_ids=["附件清单中的 source_file_id"],
     include_full_text=true
   )
   ```

7. 上一步返回 `markdown_path` 后：
   - 用户只要求“转换成 Markdown 文件发我”时，直接调用 `present_artifacts` 交付该路径，不要调用 `read_file` 把全文送入模型上下文。
   - 用户要求解读、引用或核验原文时，先针对问题中的关键名称、数字或条款调用 `grep(pattern="字面关键词", path="markdown_path", output_mode="content")` 定位行号，再用 `read_file` 读取命中位置附近的小窗口。每次调用必须显式设置 `limit <= 50`；需要扩大范围时分多次调整 `offset`，任何一次都不能读取 51 行或更多。
   - 一个关键词无法覆盖多个事实时分别定位；多个附件分别读取，并保留文件名边界。没有读到原文窗口时明确说明无法核验，不能把摘要或结构化结果改写成原文引语。
   - `grep` 命中“（一）”“（四）”等款项但当前窗口没有所属“第X条”标题时，必须继续向前读取，每次仍保持 `limit <= 50`，直到找到命中款项之前最近的“第X条”标题，并确认标题与款项之间没有另一条标题。只有读取窗口共同核验了这条父级标题和引用正文时才能标注条款号；否则只标注已核验的原文行号，绝不能根据摘要、结构化结果或相邻款项猜测父级条号。

### B. 来文检索

1. 使用用户给出的时间、分类、条目类型、标题、文号、发文单位或关键词调用 `search_incoming_documents`。
2. 没有命中时明确说明未找到，并建议用户放宽标题、单位、时间等已有条件；不要编造结果，也不要擅自切换成无条件全库查询。
3. 用户只需要列表时，保持工具返回顺序，展示当前页结果、总数、主文件是否存在和附件数量，不调用 `ask_user_question`。用户选定来文或询问详情时，再调用 `read_incoming_document(include_full_text=false)`。
4. 结果超过当前页时明确说明当前页范围；用户要求下一页时使用工具返回的 `page`、`page_size` 和 `total` 计算，不猜测页码，不一次铺开全部结果。

### C. 来文统计

用户询问数量、分布或趋势时，直接调用 `get_incoming_document_statistics`，不要先搜索，也不要用搜索结果页手工估算。

## 单篇解读输出

用户询问一份来文讲了什么时，按以下顺序回答；没有内容时写“未提取到”，不要猜测。

1. **整体结论**：用 2 至 4 句话说明来文目的、核心结论和影响。
2. **分类判断**：列出主分类；附加分类仅使用工具返回结果。
3. **关键事项**：按 `result_groups` 分组列出风险、任务、考评、奖惩或管理要求。
4. **附件范围**：列出主文件和全部附件，不把单个附件当作整份来文。
5. **依据**：普通解读可说明结构化 item 已提供的附件名和原文位置；只有用户要求原文、依据或核验时，才用 item 的 `source_file_id` 读取对应附件并引用原文片段。

## 回答约束

- 一份来文先回答整体内容，需要细节时再定位具体附件。
- 未获得真实 `incoming_id` 时，不要调用 `read_incoming_document`。
- 未获得真实 `markdown_path` 时，不要调用 `read_file` 读取附件原文。
- 引用原文或给出依据时，必须明确标注附件文件名；不能确定来源时不要声称是原文结论。
- `summary` 不是逐字原文；evidence 指向多个附件时全部列出，来源未定位时明确说明。
- `status` 不是 `ready` 时，结构化结果尚未正式发布；只能说明当前处理状态，不要推断缺失结果。
- 不要编造 `incoming_id`、`source_file_id` 或条目类型；必须使用页面上下文或工具返回值。
- 分类筛选优先使用工具返回的稳定 ID；中文名称只用于面向用户显示。未知分类根据工具返回的支持列表修正，不要猜测。
