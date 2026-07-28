---
name: 流程图
slug: flowchart
description: 当用户需要把业务步骤、审批、判断分支或流程流转制作成静态流程图时使用。
---

# 流程图
1. 用 `write_file` 写入 `outputs/.visualization-specs/<name>.flow.json`，只使用下方字段；不要使用 `type`、`title`、`from`、`to` 或 `branches`。
2. `nodes` 的每项必须有 `id`、`kind`、`label`；`kind` 只能是 `start`、`process`、`decision`、`end`。`edges` 的每项使用 `source`、`target`，判断分支把“是/否”写入 `label`。
3. 必须恰有一个 `start`，至少一个 `end`；每个 `decision` 恰有两条出边，所有节点都必须能从开始到达并最终到达结束。
4. 直接套用此模板后替换文字和节点 ID：

```json
{
  "nodes": [
    {"id": "start", "kind": "start", "label": "开始"},
    {"id": "review", "kind": "process", "label": "审核资料"},
    {"id": "complete", "kind": "decision", "label": "资料完整？"},
    {"id": "supplement", "kind": "process", "label": "补充资料"},
    {"id": "end", "kind": "end", "label": "结束"}
  ],
  "edges": [
    {"source": "start", "target": "review"},
    {"source": "review", "target": "complete"},
    {"source": "complete", "target": "end", "label": "是"},
    {"source": "complete", "target": "supplement", "label": "否"},
    {"source": "supplement", "target": "end"}
  ]
}
```

5. `output_name` 只能使用 1 至 80 个 ASCII 字母、数字、下划线或短横线，不含扩展名；中文标题也要转换为英文文件名，如 `application-approval-process`。
6. 调用 `render_flowchart`，成功后调用 `present_artifacts`。不生成 DOT、HTML 或 Graphviz 属性。
