---
name: 流程图
slug: flowchart
description: 用户明确要求流程图、审批流程或业务流程图时必须先读取此 Skill；只用 render_flowchart 生成 SVG，禁止改用文档生成工具或手写 SVG。
---

# 流程图
1. 用 `write_file` 写入 `outputs/.visualization-specs/<name>.flow.json`，只使用下方字段；不要使用 `type`、`title`、`from`、`to` 或 `branches`。
2. `nodes` 的每项必须有 `id`、`kind`、`label`；`id` 以 ASCII 字母开头且只使用字母、数字、下划线或短横线；`kind` 只能是 `start`、`process`、`decision`、`end`。`edges` 的每项使用 `source`、`target`，判断分支把“是/否”写入 `label`。
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

5. `output_name` 使用 1 至 80 个中英文字母、数字、下划线或短横线，不含扩展名；用户明确指定名称时原样使用，不要翻译或改写，如 `申请审批流程-0730`。
6. 调用 `render_flowchart`；成功后系统自动展示 SVG，不再调用 `present_artifacts`。不生成 D2、DOT、HTML 或渲染器属性。
