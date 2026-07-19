---
name: build-risk-ledger
slug: build-risk-ledger
description: "按时间范围汇总多份来文中的风险事项、管理要求和任务要求。当用户要求跨来文风险汇总、风险清单、风险台账、整改台账、防控措施或导出风险 XLSX 时使用此技能。"
---

# 风险台账生成

只使用来文工具返回的正式结构化结果。统计按来文数计算；台账一行表示一条结构化事项，不把文档数和事项数混为一谈。

## 术语

- **来文**：一条由 `incoming_id` 标识的完整业务记录，可包含主文件和多个附件。
- **文件**：来文中的主文件或附件，由 `source_file_id` 标识。来文数、文件数和结构化事项数是不同口径。

## 固定流程

每次只调用一个工具，检查返回结果后再继续。

1. 确定时间范围。用户明确要求“全部”时省略日期；“本月、今年”等相对时间按当前日期换算；确实无法确定时调用 `ask_user_question`。
2. 调用精确统计：

   ```text
   get_incoming_document_statistics(
     date_from="YYYY-MM-DD",
     date_to="YYYY-MM-DD",
     item_types=["risk_item", "management_requirement_item", "task_item"]
   )
   ```

3. 用户只询问数量或分布时，直接使用统计结果回答并停止。`total=0` 时直接报告无命中结果，不再查询。`total>100` 且用户没有明确要求全量时，调用 `ask_user_question` 让用户选择缩小时间范围或继续全量处理。
4. 调用查询定位来文：

   ```text
   search_incoming_documents(
     date_from="YYYY-MM-DD",
     date_to="YYYY-MM-DD",
     item_types=["risk_item", "management_requirement_item", "task_item"],
     page=1,
     page_size=50
   )
   ```

5. 工具返回的累计数量小于 `total` 时，页码加一继续查询。用户明确要求全量时必须取得全部页面；不要用第一页数量代替总数。
6. 对每个真实 `incoming_id` 逐一调用：

   ```text
   read_incoming_document(incoming_id="真实 incoming_id", include_full_text=false)
   ```

7. 只收集 `risk_item`、`management_requirement_item`、`task_item` 三类 detail。每行保留：
   - 事项类型；
   - 风险或要求名称；
   - 涉及部门、专业、岗位；
   - 风险类型或任务类型；
   - 管理要求、整改措施或截止时间；
   - 来文标题、文号、来文日期、`incoming_id`；
   - 全部 `evidence.file_name` 和 `evidence.quote`，不要只保留第一条。
8. 相同来文中的不同 detail 分行保留。只有事项类型、事项名称、来源附件和原文依据都相同时才去重。
9. evidence 已包含附件名和原文片段时不要读取全文。用户要求核验时，按 evidence 中的真实 `source_file_id` 读取对应附件；没有 `source_file_id` 时标注“待核验”，不要自动读取全部附件。

## 输出格式

先给出统计口径，再给风险摘要和台账。

- **统计口径**：时间范围、命中来文数、各条目类型的文档数和 detail 数，数值直接使用统计工具结果。
- **风险摘要**：归纳主要风险、集中涉及对象、明确期限和后续要求；没有数据时明确写“未提取到”。
- **台账表**：至少包含“序号、事项类型、事项、涉及对象、时间/期限、措施/要求、来文、附件、原文依据”。

用户要求 XLSX 时：

1. 调用 `generate_xlsx`，文件名使用“风险台账-起始日期-结束日期.xlsx”。
2. 创建“统计”和“风险台账”两个工作表；第一行必须是表头，不使用合并单元格。
3. 日期未限定时文件名范围使用“全部”。所有单元格只能是字符串或数字；多个附件和引用用换行拼成字符串，不传入 list 或 dict。
4. 工具返回 outputs 文件路径后，调用 `present_artifacts` 交付。
5. 未成功生成文件时明确报错，不声称已交付。

## 禁止事项

- 不根据常识补写部门、专业、岗位、期限或措施。
- 不把 `search_incoming_documents.items` 的数量当作精确统计。
- 不把同一来文的多条事项强行合并为一条。
- 不输出没有附件名和原文片段支持的确定性结论；证据缺失时标注“待核验”。
