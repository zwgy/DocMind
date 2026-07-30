---
name: 数据图表
slug: data-chart
description: 用户明确要求数据图表、柱状图、折线图、面积图、饼图或散点图时必须先读取此 Skill；只用 render_data_chart 生成 SVG，禁止改用文档生成工具或手写 SVG。
---

# 数据图表

1. 将数据准备为当前会话中的 UTF-8 CSV；大型结果只返回路径、列名、类型、行数和一条样本。
2. 柱状图、折线图、面积图使用 `category` 和 `values`；饼图使用 `name` 和 `value`；散点图使用 `x` 和 `y`。
3. 只在需要选择分类序列、占比或相关性规则时读取一个对应 reference。
4. `output_name` 只能使用 1 至 80 个 ASCII 字母、数字、下划线或短横线，不含扩展名；中文标题也要转换为英文文件名，如 `monthly-sales`。
5. 调用 `render_data_chart`；成功后系统自动展示 SVG，不再调用 `present_artifacts`。
6. 不生成 ECharts Option、SVG 或 JavaScript；字段错误时只修正一次参数。
