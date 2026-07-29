---
name: summarize-assessment-actions
slug: summarize-assessment-actions
description: "汇总指定时间范围内多份来文中的通报、考评、表彰、奖励、处罚、批评和问责事项。当用户要求跨来文通报汇总、考评汇总、奖惩汇总、后续整改要求或导出 Markdown、DOCX、XLSX 时使用此技能。"
---

# 通报考评奖惩汇总

只使用来文工具返回的正式结构化结果。每项结论必须能追溯到来文、附件和原文依据。

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
     classifications=["notification", "assessment", "reward_punishment"]
   )
   ```

3. 用户只询问数量或分布时，直接使用统计结果回答并停止。`total=0` 时直接报告无命中结果，不再查询。`total>100` 且用户没有明确要求全量时，调用 `ask_user_question` 让用户选择缩小时间范围或继续全量处理。
4. 调用查询：

   ```text
   search_incoming_documents(
     date_from="YYYY-MM-DD",
     date_to="YYYY-MM-DD",
     classifications=["notification", "assessment", "reward_punishment"],
     page=1,
     page_size=50
   )
   ```

5. 工具返回的累计数量小于 `total` 时继续翻页。用户明确要求全量时必须取得全部页面。
6. 对每个真实 `incoming_id` 逐一调用：

   ```text
   read_incoming_document(incoming_id="真实 incoming_id", include_full_text=false)
   ```

7. 收集并分别处理：
   - `assessment_item`：对象、项目、原因、结果；
   - `reward_punishment_item`：对象、处置类型、原因、结果、后续要求；
   - `task_item`：从命中的通报、考评或奖惩来文中收集后续任务、整改要求和期限。
8. 每项都保留来文标题、文号、日期、`incoming_id`，以及全部 `evidence.file_name` 和 `evidence.quote`。
9. evidence 已包含附件名和原文片段时不要读取全文。用户要求核验时，按 evidence 中的真实 `source_file_id` 读取对应附件；没有 `source_file_id` 时标注“待核验”，不要自动读取全部附件。

## 输出格式

1. **汇总范围**：时间范围、命中来文数，以及考评、奖惩和任务条目类型的文档数与 detail 数。
2. **总体结论**：只归纳数据中重复出现的对象、原因、结果和要求，不推断趋势原因。
3. **考评事项**：对象｜项目｜原因｜结果｜来文｜附件｜原文依据。
4. **奖惩通报**：对象｜处置类型｜原因｜结果｜后续要求｜来文｜附件｜原文依据。
5. **后续任务**：任务｜责任对象｜期限｜来文｜附件｜原文依据。

用户要求文件时只生成其指定格式：

- 文件名使用“通报考评奖惩汇总-起始日期-结束日期”；日期未限定时范围使用“全部”。
- 生成 DOCX 或 XLSX 前先读取依赖 Skill `office-export` 的入口文件，由它选择用户指定格式的契约。
- DOCX：使用标题、段落和真正的 Word 表格组织汇总范围、考评事项、奖惩通报与后续任务。
- XLSX：创建“统计、考评事项、奖惩通报、后续任务”工作表；第一行是表头，多个附件和引用用换行拼成一个字符串。
- DOCX 或 XLSX 定义写入当前会话后调用 `export_office_file`，使用对应 `output_format`。
- Markdown：用 `write_file` 写入 `/home/gem/user-data/outputs/通报考评奖惩汇总-范围.md`。
- 文件生成成功后调用 `present_artifacts` 交付；失败时不要声称已交付。

## 禁止事项

- 不编造对象、原因、结果、后续要求、来文或附件。
- 不用搜索结果数量代替统计工具结果。
- 不把 `task_item` 单独出现就判断为通报、考评或奖惩事项。
- 不把没有 evidence 的内容写成确定性原文结论；标注“待核验”。
- 用户未要求文件时只在对话中回答，不生成文件。
