---
name: Office 文件导出
slug: office-export
description: 用户要求生成或导出 DOCX、Word、PDF、XLSX、Excel 文件，或要求把当前会话中的图片、图表、流程图、思维导图插入这些文件时必须先读取此 Skill。
---

# Office 文件导出

这是当前 Agent 直接完成的确定性导出流程，不调用 `task` 委派读取 reference、生成定义、导出或验收。任务还包含资料检索、事实核验等多个步骤时，先用 `write_todos` 制定计划并在每步完成后更新状态。

1. 只读取用户所需格式对应的一个 reference；直接读取系统给出的路径，不列目录、不搜索或重复读取 Skill：
   - DOCX/Word：`references/docx.md`
   - PDF：`references/pdf.md`
   - XLSX/Excel：`references/xlsx.md`
2. 严格复制 reference 的顶层结构：DOCX/PDF 必须以 `{"kind":"document","blocks":[...]}` 开始，XLSX 必须以 `{"kind":"workbook","sheets":[...]}` 开始。不要自创 `type`、`version`、`page_setup` 等字段。
3. 在 `outputs/.office-definitions/<name>.json` 一次写入 UTF-8 JSON 定义；大型正文和表格不得直接塞入工具参数。写入后不使用 `read_file`、`grep`、`glob` 或 `execute` 检查定义。
4. 图片必须引用当前会话 `workspace`、`uploads` 或 `outputs` 下的虚拟路径，不传宿主路径、URL 或 Base64。
5. 图表、流程图和思维导图保持 SVG；Office 导出工具会在确有需要时临时转换，不要提前生成 PNG。
6. `output_name` 使用不含路径和扩展名的文件名，可使用中文。
7. 写入后立即调用 `export_office_file`，原样传入 `write_file` 返回的 definition 完整路径。
8. `export_office_file` 成功后文件已由系统自动交付；将剩余 Todo 更新为完成并直接回答，不要读取产物、不要重复调用 `present_artifacts`，也不要继续检查 Skill。失败时只依据错误修改同一份定义并重试一次；仍失败则明确报告，不声称已经交付。
9. 用户要求多个格式时逐个生成定义并串行导出，每次只调用一个工具。
