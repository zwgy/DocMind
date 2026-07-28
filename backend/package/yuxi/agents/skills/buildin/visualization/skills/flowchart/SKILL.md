---
name: 流程图
slug: flowchart
description: 当用户需要把业务步骤、审批、判断分支或流程流转制作成静态流程图时使用。
---

# 流程图

1. 用 `write_file` 写入 `outputs/.visualization-specs/<name>.flow.json`。
2. 定义一个开始节点、至少一个结束节点；判断节点使用带“是/否”等文字的分支。
3. 调用 `render_flowchart`，成功后调用 `present_artifacts`。
4. 不生成 DOT、HTML 或 Graphviz 属性。
