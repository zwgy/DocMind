# XLSX 定义

使用 `kind=workbook`。每个工作表包含：

- `name`：1 至 31 个字符，不含 `[]:*?/\\`
- `rows`：二维单元格数组，值只能是字符串、数字、布尔值或 `null`
- `header_rows`：表头行数，默认 1；表头会加粗并使用浅色背景
- `freeze_panes`：可选冻结位置，例如 `A2`
- `images`：可选图片列表，使用当前会话 `source_path`、单元格 `anchor` 和 64 至 2400 的 `width_px`

不要传公式；等号、加号、减号或 `@` 开头的字符串会按普通文本写入。多个附件名或原文引用使用
换行拼成一个字符串，不传嵌套对象。

完整示例：

```json
{
  "kind": "workbook",
  "sheets": [
    {
      "name": "统计",
      "header_rows": 1,
      "freeze_panes": "A2",
      "rows": [
        ["类型", "数量"],
        ["风险项", 12],
        ["整改项", 5]
      ],
      "images": [
        {
          "source_path": "/home/gem/user-data/outputs/risk-summary.svg",
          "anchor": "D2",
          "width_px": 900
        }
      ]
    },
    {
      "name": "风险台账",
      "header_rows": 1,
      "rows": [
        ["序号", "风险", "责任部门"],
        [1, "巡检记录不完整", "生产部"]
      ]
    }
  ]
}
```

调用：

```text
export_office_file(
  definition_path="write_file 返回的完整 JSON 路径",
  output_format="xlsx",
  output_name="风险台账"
)
```

不要使用合并单元格、宏、外部链接或 Base64 图片。
