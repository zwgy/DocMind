---
name: incoming-document
slug: incoming-document
description: "查询、读取和统计已接入系统的来文。当用户询问当前来文、历史来文、分类结果、结构化业务事项或需要核验附件原文时使用此技能。"
---

# 来文查询技能

用户询问当前来文、历史来文、分类、业务事项、数量分布或附件原文时使用本技能。默认先使用来文级摘要和结构化结果，只有信息不足或需要原文依据时才读取具体附件。

## 可用工具

- `search_incoming_documents`：按来文日期、主分类、条目类型、标题、文号或附件名分页查找来文。
- `read_incoming_document`：读取一份来文的整体结论、附件清单和正式结构化结果；指定附件后可将 Markdown 写入当前会话目录。
- `get_incoming_document_statistics`：按与查询相同的条件统计来文，并按分类、条目类型和月份聚合。
- `read_file`：读取 `read_incoming_document` 返回的 `markdown_path`，用于核验附件原文。

条目类型参数支持内部 ID 或当前中文名称。工具返回的 `item_type_labels` 是当前有效映射；未知类型不要猜测，应依据工具返回的支持列表修正查询。

## 固定流程

每次只执行一步。每次工具返回后先检查结果，再决定下一步。

1. 当前页面已有来文级摘要和结构化结果时，先据此回答，不要立即读取全文。
2. 不知道 `incoming_id` 时，先搜索：

   ```text
   search_incoming_documents(keyword="用户关键词", page=1, page_size=20)
   ```

3. 从搜索结果取得真实 `incoming_id`，再读取来文级详情：

   ```text
   read_incoming_document(incoming_id="工具返回的 incoming_id", include_full_text=false)
   ```

4. 只有信息不足、用户要求原文依据或必须核验细节时，才从附件清单选择真实 `source_file_id`：

   ```text
   read_incoming_document(
     incoming_id="工具返回的 incoming_id",
     source_file_ids=["附件清单中的 source_file_id"],
     include_full_text=true
   )
   ```

5. 上一步返回 `markdown_path` 后，使用 `read_file` 分段读取。多个附件分别读取，并保留文件名边界。
6. 用户询问数量、分布或趋势时，直接调用 `get_incoming_document_statistics`，不要用搜索结果页手工估算。

## 回答约束

- 一份来文先回答整体内容，需要细节时再定位具体附件。
- 未获得真实 `incoming_id` 时，不要调用 `read_incoming_document`。
- 未获得真实 `markdown_path` 时，不要调用 `read_file` 读取附件原文。
- 引用原文或给出依据时，必须明确标注附件文件名；不能确定来源时不要声称是原文结论。
- `status` 不是 `ready` 时，结构化结果尚未正式发布；只能说明当前处理状态，不要推断缺失结果。
- 不要编造 `incoming_id`、`source_file_id` 或条目类型；必须使用页面上下文或工具返回值。
