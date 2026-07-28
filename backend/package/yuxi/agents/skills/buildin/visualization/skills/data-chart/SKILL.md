---
name: 数据图表
slug: data-chart
description: 当用户需要依据 CSV、查询结果或小型表格生成柱状图、折线图、面积图、饼图或散点图时使用。
---

# 数据图表

1. 将数据准备为当前会话中的 UTF-8 CSV；大型结果只返回路径、列名、类型、行数和一条样本。
2. 柱状图、折线图、面积图使用 `category` 和 `values`；饼图使用 `name` 和 `value`；散点图使用 `x` 和 `y`。
3. 只在需要选择分类序列、占比或相关性规则时读取一个对应 reference。
4. 调用 `render_data_chart` 后调用 `present_artifacts` 交付 SVG。
5. 不生成 ECharts Option、SVG 或 JavaScript；字段错误时只修正一次参数。
