# DOCX 定义

使用 `kind=document`，`blocks` 按文档中的实际出现顺序排列。

支持的 block：

- `heading`：`text` 和 1 至 4 的 `level`
- `paragraph`：`text`
- `table`：等宽二维 `rows`，`header=true` 时首行作为表头
- `image`：当前会话图片 `source_path`、可选 `caption`、2 至 17 的 `width_cm`
- `page_break`：另起一页

表格单元格只能是字符串、数字、布尔值或 `null`。图片支持 SVG、PNG、JPEG、GIF、BMP、TIFF、WebP；
SVG 会在写入 Word 时临时转换为高分辨率 PNG。`width_cm` 是最大宽度；导出器还会按页面可用高度
等比缩小纵向长图，为标题和图题保留空间，不会裁剪图片。

完整示例：

```json
{
  "kind": "document",
  "title": "安全检查报告",
  "blocks": [
    {"type": "heading", "level": 1, "text": "检查结论"},
    {"type": "paragraph", "text": "本次检查共发现 2 项待整改问题。"},
    {
      "type": "table",
      "header": true,
      "rows": [
        ["序号", "问题", "责任部门"],
        [1, "巡检记录不完整", "生产部"],
        [2, "复核节点缺失", "安全部"]
      ]
    },
    {
      "type": "image",
      "source_path": "/home/gem/user-data/outputs/inspection-flow.svg",
      "caption": "图 1 检查流程",
      "width_cm": 16
    }
  ]
}
```

调用：

```text
export_office_file(
  definition_path="write_file 返回的完整 JSON 路径",
  output_format="docx",
  output_name="安全检查报告"
)
```

不要把 Markdown、HTML、图片 Base64 或 ECharts Option 放进定义。
