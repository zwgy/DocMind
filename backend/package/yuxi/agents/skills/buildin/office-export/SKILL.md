---
name: Office 文件导出
slug: office-export
description: 用户要求生成或导出 DOCX、Word、PDF、XLSX、Excel 文件，或要求把当前会话中的图片、图表、流程图、思维导图插入这些文件时必须先读取此 Skill。
---

# Office 文件导出

1. 只读取用户所需格式对应的一个 reference：
   - DOCX/Word：`references/docx.md`
   - PDF：`references/pdf.md`
   - XLSX/Excel：`references/xlsx.md`
2. 按 reference 在 `outputs/.office-definitions/<name>.json` 写入 UTF-8 JSON 定义；大型正文和表格不得直接塞入工具参数。
3. 图片必须引用当前会话 `workspace`、`uploads` 或 `outputs` 下的虚拟路径，不传宿主路径、URL 或 Base64。
4. 图表、流程图和思维导图保持 SVG；Office 导出工具会在确有需要时临时转换，不要提前生成 PNG。
5. `output_name` 使用不含路径和扩展名的文件名，可使用中文。
6. 调用 `export_office_file`，原样传入 `write_file` 返回的 definition 完整路径。
7. 工具成功返回后调用 `present_artifacts`；失败时根据错误修正一次，仍失败则明确报告，不声称已经交付。
8. 用户要求多个格式时逐个生成定义并串行导出，每次只调用一个工具。
