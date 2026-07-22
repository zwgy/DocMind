---
name: incoming-document
slug: incoming-document
description: "查询、读取、统计和综合解读已接入系统的来文。当用户询问某份来文讲了什么、当前或历史来文、分类结果、结构化业务事项、数量分布或需要核验附件原文时使用此技能。"
---

# 来文查询与解读技能

用户询问当前来文、历史来文、分类、业务事项、数量分布或附件原文时使用本技能。默认先使用来文级摘要和结构化结果，只有信息不足或需要原文依据时才读取具体附件。

## 术语

- **来文**：一条完整业务记录，由唯一 `incoming_id` 标识，包含来文级摘要、分类、结构化结果和文件清单。
- **文件**：来文中的主文件或附件，每个文件由 `source_file_id` 标识并拥有独立原文。不要把单个文件当作整份来文。

## 可用工具

- `search_incoming_documents`：按来文日期、主分类、条目类型、标题、文号或附件名分页查找来文。
- `read_incoming_document`：`include_full_text=false` 时读取来文整体结论、附件清单和正式结构化结果；`include_full_text=true` 时只将指定附件的 Markdown 写入当前会话目录并返回路径。
- `get_incoming_document_statistics`：按与查询相同的条件统计来文，并按分类、条目类型和月份聚合。
- `ask_user_question`：搜索命中多份来文或关键范围不明确时，请用户选择。
- `read_file`：读取 `read_incoming_document` 返回的 `markdown_path`，用于核验附件原文。
- `present_artifacts`：把已写入当前线程 outputs 的 Markdown 文件作为交付物展示给用户。

条目类型参数支持内部 ID 或当前中文名称。工具返回的 `item_type_labels` 是当前有效映射；未知类型不要猜测，应依据工具返回的支持列表修正查询。

## 固定流程

先判断用户任务，只执行对应分支。每次只调用一个工具，检查返回后再继续。

### A. 单篇来文解读

1. 检查页面上下文是否同时包含来文级摘要、分类、结构化结果、附件清单，以及 item 的附件名、原文位置和 `source_file_id`；信息完整时直接回答，不调用工具。
2. 已知真实 `incoming_id` 但上述信息不完整时，读取来文级详情：

   ```text
   read_incoming_document(incoming_id="真实 incoming_id", include_full_text=false)
   ```

3. 不知道 `incoming_id` 时，先搜索：

   ```text
   search_incoming_documents(keyword="用户关键词", page=1, page_size=20)
   ```

4. 搜索只命中一份，或用户选定一份后，再使用真实 `incoming_id` 读取详情。命中多份时调用 `ask_user_question`，选项必须来自搜索结果并携带真实 `incoming_id`；不要自行选择。
5. 用户要求交付 Markdown 文件，或需要原文依据、具体附件内容、核验细节时，从页面上下文或附件清单选择真实 `source_file_id`。如果页面上下文已经提供真实 `incoming_id` 和 `source_file_id`，直接调用原文模式，不要先重复读取来文详情：

   ```text
   read_incoming_document(
     incoming_id="真实 incoming_id",
     source_file_ids=["附件清单中的 source_file_id"],
     include_full_text=true
   )
   ```

6. 上一步返回 `markdown_path` 后：
   - 用户只要求“转换成 Markdown 文件发我”时，直接调用 `present_artifacts` 交付该路径，不要调用 `read_file` 把全文送入模型上下文。
   - 用户要求解读、引用或核验原文时，才使用 `read_file` 分段读取；多个附件分别读取，并保留文件名边界。

### B. 来文检索

1. 使用用户给出的时间、分类、条目类型或关键词调用 `search_incoming_documents`。
2. 用户只需要列表时直接返回搜索结果；用户选定来文或询问详情时，再调用 `read_incoming_document(include_full_text=false)`。
3. 需要下一页时使用工具返回的 `page`、`page_size` 和 `total` 计算，不猜测页码。

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
