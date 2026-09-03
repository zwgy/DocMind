---
name: 流程图
slug: flowchart
description: 用户明确要求流程图、审批流程或业务流程图时必须先读取此 Skill；只用 render_flowchart 生成 SVG，禁止改用文档生成工具或手写 SVG。
---

# 流程图
1. 直接调用 `render_flowchart`，把流程定义放入 `definition`；不调用 `task` 或其他子智能体代写流程定义，不调用 `ls`、`write_file`、`edit_file` 或 `execute`，不创建中间 JSON 文件。
2. 只画用户明确要求的范围，不扩展整篇材料中的其他流程。用户未指定复杂度时使用 2 至 10 个节点、至多一个判断节点，不画循环，不添加没有连线的节点。
3. `definition.nodes` 的每项必须有 `id`、`kind`、`label`；`id` 是 1 至 64 个字符的唯一短文本，边的 `source`、`target` 必须原样引用它；`kind` 只能是 `start`、`process`、`decision`、`end`。判断分支把条件写入边的 `label`。
4. 必须恰有一个 `start`，至少一个 `end`；`start` 无入边，`end` 无出边，每个 `decision` 至少有两条出边；每个节点都必须能从开始到达并最终到达结束。不同分支可直接指向同一个后续节点，不创建空标签的汇合节点。
5. 直接套用此工具调用模板后替换文字和节点 ID：

```json
{
  "definition": {
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
    ],
    "direction": "TB"
  },
  "output_name": "申请审批流程-0730"
}
```

6. 调用前逐项检查：节点 ID 唯一；每条边都引用已有节点；除 `start` 外均有入边；除 `end` 外均有出边；不存在孤立节点或回到 `start` 的边。
7. `output_name` 使用 1 至 80 个中英文字母、数字、下划线或短横线，不含扩展名；用户明确指定名称时原样使用，不要翻译或改写，如 `申请审批流程-0730`。
8. 校验或渲染失败时，不在旧参数上逐项修补；从上方模板重新构造节点更少的完整 `definition`，最多重试两次，仍失败则说明错误并停止。
9. 成功后系统自动展示 SVG，不再调用 `present_artifacts`。不生成 D2、DOT、HTML 或渲染器属性。
