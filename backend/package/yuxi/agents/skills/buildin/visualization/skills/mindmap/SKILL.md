---
name: 思维导图
slug: mindmap
description: 当用户需要把主题、方案、会议要点或文档摘要整理为层级化静态思维导图时使用。
---

# 思维导图

1. 用 `write_file` 写入 `outputs/.visualization-specs/<name>.mindmap.md`。
2. 使用两个空格一级的 Markdown 无序列表；第一项是唯一中心主题。
3. `output_name` 只能使用 1 至 80 个 ASCII 字母、数字、下划线或短横线，不含扩展名；中文标题也要转换为英文文件名，如 `project-kickoff-mindmap`。
4. 调用 `render_mindmap`，可选 `horizontal` 或 `radial`，成功后调用 `present_artifacts`。
5. 节点使用短语，不写 HTML、链接、图片或脚本。
