from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

_NODE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_ALLOWED_KINDS = {"start", "process", "decision", "end"}
_CLASS_STYLES = {
    "start": (
        "shape: oval",
        'style.fill: "#E7F4EC"',
        'style.stroke: "#2F6F5E"',
        'style.font-color: "#214E43"',
    ),
    "process": (
        "shape: rectangle",
        'style.fill: "#EEF3F8"',
        'style.stroke: "#4F6F8F"',
        'style.font-color: "#263746"',
        "style.border-radius: 8",
    ),
    "decision": (
        "shape: diamond",
        'style.fill: "#FFF3DF"',
        'style.stroke: "#B56B2D"',
        'style.font-color: "#754319"',
    ),
    "end": (
        "shape: oval",
        'style.fill: "#F7E9EC"',
        'style.stroke: "#9B4D5B"',
        'style.font-color: "#6F3440"',
        "style.stroke-width: 3",
    ),
}


def _validate_flow(data: dict) -> tuple[list[dict], list[dict]]:
    nodes, edges = data.get("nodes"), data.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("流程定义必须包含 nodes 和 edges")
    if not 2 <= len(nodes) <= 80 or len(edges) > 160:
        raise ValueError("流程图节点数量必须在 2 到 80 之间，边不超过 160 条")

    ids = {node.get("id") for node in nodes}
    if len(ids) != len(nodes) or None in ids:
        raise ValueError("节点 ID 必须唯一")
    if any(not isinstance(node.get("id"), str) or not _NODE_ID_RE.fullmatch(node["id"]) for node in nodes):
        raise ValueError("节点 ID 必须以 ASCII 字母开头，且只包含字母、数字、下划线或短横线")
    if any(
        node.get("kind") not in _ALLOWED_KINDS
        or not isinstance(node.get("label"), str)
        or not node["label"].strip()
        or len(node["label"]) > 80
        for node in nodes
    ):
        raise ValueError("节点类型或文本不符合要求")

    starts = [node for node in nodes if node.get("kind") == "start"]
    ends = [node for node in nodes if node.get("kind") == "end"]
    if len(starts) != 1 or not ends:
        raise ValueError("流程必须且只能有一个开始节点，并至少有一个结束节点")

    seen_edges = set()
    for edge in edges:
        if edge.get("source") not in ids or edge.get("target") not in ids:
            raise ValueError("存在指向不存在节点的边")
        key = (edge.get("source"), edge.get("target"), edge.get("label", ""))
        if key in seen_edges:
            raise ValueError("不能包含重复的边")
        seen_edges.add(key)
        if not isinstance(edge.get("label", ""), str) or len(edge.get("label", "")) > 40:
            raise ValueError("边标签必须是 40 字以内的文本")

    outgoing = {node_id: [] for node_id in ids}
    incoming = {node_id: [] for node_id in ids}
    for edge in edges:
        outgoing[edge["source"]].append(edge["target"])
        incoming[edge["target"]].append(edge["source"])
    if incoming[starts[0]["id"]]:
        raise ValueError("开始节点不能有入边")
    if any(outgoing[end["id"]] for end in ends):
        raise ValueError("结束节点不能有出边")
    for node in nodes:
        if node["kind"] == "decision" and len(outgoing[node["id"]]) != 2:
            raise ValueError("判断节点必须恰好有两条出边")

    reachable = set()
    pending = [starts[0]["id"]]
    while pending:
        node_id = pending.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        pending.extend(outgoing[node_id])
    if reachable != ids or not any(end["id"] in reachable for end in ends):
        raise ValueError("所有节点必须可从开始节点到达")

    can_finish = set()
    pending = [end["id"] for end in ends]
    while pending:
        node_id = pending.pop()
        if node_id in can_finish:
            continue
        can_finish.add(node_id)
        pending.extend(incoming[node_id])
    if can_finish != ids:
        raise ValueError("所有节点都必须能够到达结束节点")
    return nodes, edges


def _quoted(value: str) -> str:
    # JSON 字符串转义是 D2 双引号字符串接受的安全子集，避免节点文案越过语法边界。
    return json.dumps(value.strip(), ensure_ascii=False)


def _build_d2(data: dict) -> str:
    nodes, edges = _validate_flow(data)
    direction = data.get("direction", "TB")
    if direction not in {"TB", "LR"}:
        raise ValueError("direction 只能是 TB 或 LR")

    # 外部 node id 只参与关系校验；渲染源码使用顺序生成的内部 ID，从根本上隔离
    # D2 关键字和未来语法扩展对用户定义的影响。
    identifiers = {node["id"]: f"node_{index}" for index, node in enumerate(nodes, start=1)}
    lines = [f"direction: {'down' if direction == 'TB' else 'right'}", "classes: {"]
    for kind, styles in _CLASS_STYLES.items():
        lines.append(f"  {kind}_node: {{")
        lines.extend(f"    {style}" for style in styles)
        lines.append("  }")
    lines.append("}")

    for node in nodes:
        lines.extend(
            [
                f"{identifiers[node['id']]}: {_quoted(node['label'])} {{",
                f"  class: {node['kind']}_node",
                "}",
            ]
        )
    for edge in edges:
        statement = f"{identifiers[edge['source']]} -> {identifiers[edge['target']]}"
        if edge.get("label"):
            statement += f": {_quoted(edge['label'])}"
        lines.append(statement)
    return "\n".join(lines) + "\n"


def _render_d2(source: str, output: Path) -> None:
    token = uuid.uuid4().hex
    d2_source = output.with_name(f".{token}.d2")
    rendered = output.with_name(f".{token}.svg")
    d2_source.write_text(source, encoding="utf-8")
    try:
        result = subprocess.run(
            ["d2", "--layout=dagre", "--theme=0", str(d2_source), str(rendered)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if result.returncode != 0 or not rendered.is_file():
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise RuntimeError((detail[-1] if detail else "D2 未生成 SVG")[:300])
        os.replace(rendered, output)
    finally:
        d2_source.unlink(missing_ok=True)
        rendered.unlink(missing_ok=True)


def main() -> None:
    request = json.load(sys.stdin)
    data = request["definition"]
    nodes, _edges = _validate_flow(data)
    _render_d2(_build_d2(data), Path(request["output"]))
    print(json.dumps({"summary": f"已生成流程图，共 {len(nodes)} 个节点"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
