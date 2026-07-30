# PDF 定义

PDF 使用与 DOCX 相同的 `kind=document` block 定义，由导出服务先生成 DOCX，再通过本地
LibreOffice 转换为 PDF。不要另外生成 HTML 或调用浏览器打印。

支持的 block：

- `heading`：`text` 和 1 至 4 的 `level`
- `paragraph`：`text`
- `table`：等宽二维 `rows`，`header=true` 时首行作为表头
- `image`：当前会话图片 `source_path`、可选 `caption`、2 至 17 的 `width_cm`
- `page_break`：另起一页

`width_cm` 是图片最大宽度；导出器会同时按 PDF 页面可用高度等比缩小纵向长图，不会裁剪图片。

完整示例：

```json
{
  "kind": "document",
  "title": "月度经营分析",
  "blocks": [
    {"type": "heading", "level": 1, "text": "销售趋势"},
    {"type": "paragraph", "text": "本月销售额保持稳定增长。"},
    {
      "type": "image",
      "source_path": "/home/gem/user-data/outputs/monthly-sales.svg",
      "caption": "图 1 月度销售趋势",
      "width_cm": 16
    },
    {"type": "page_break"},
    {"type": "heading", "level": 1, "text": "后续安排"}
  ]
}
```

调用：

```text
export_office_file(
  definition_path="write_file 返回的完整 JSON 路径",
  output_format="pdf",
  output_name="月度经营分析"
)
```

图片保持当前会话虚拟路径，不提前转 PNG，不把最终 PDF 再转换一次。
