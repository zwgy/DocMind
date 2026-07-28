from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path

request = json.load(sys.stdin)
data = json.loads(Path(request["source_path"]).read_text(encoding="utf-8"))
nodes, edges = data.get("nodes"), data.get("edges")
if not isinstance(nodes, list) or not isinstance(edges, list): raise ValueError("流程定义必须包含 nodes 和 edges")
if not 2 <= len(nodes) <= 80 or len(edges) > 160: raise ValueError("流程图节点数量必须在 2 到 80 之间，边不超过 160 条")
ids = {node.get("id") for node in nodes}
if len(ids) != len(nodes) or None in ids: raise ValueError("节点 ID 必须唯一")
if any(not isinstance(node.get("id"), str) or not node["id"].strip() or len(node["id"]) > 64 for node in nodes): raise ValueError("节点 ID 必须是非空短文本")
allowed_kinds = {"start", "process", "decision", "end"}
if any(node.get("kind") not in allowed_kinds or not isinstance(node.get("label"), str) or not node["label"].strip() or len(node["label"]) > 80 for node in nodes): raise ValueError("节点类型或文本不符合要求")
starts = [node for node in nodes if node.get("kind") == "start"]
ends = [node for node in nodes if node.get("kind") == "end"]
if len(starts) != 1 or not ends: raise ValueError("流程必须且只能有一个开始节点，并至少有一个结束节点")
seen_edges = set()
for edge in edges:
    if edge.get("source") not in ids or edge.get("target") not in ids: raise ValueError("存在指向不存在节点的边")
    key = (edge.get("source"), edge.get("target"), edge.get("label", ""))
    if key in seen_edges: raise ValueError("不能包含重复的边")
    seen_edges.add(key)
    if not isinstance(edge.get("label", ""), str) or len(edge.get("label", "")) > 40: raise ValueError("边标签必须是 40 字以内的文本")
outgoing = {node_id: [] for node_id in ids}
incoming = {node_id: [] for node_id in ids}
for edge in edges:
    outgoing[edge["source"]].append(edge["target"])
    incoming[edge["target"]].append(edge["source"])
if incoming[starts[0]["id"]]: raise ValueError("开始节点不能有入边")
if any(outgoing[end["id"]] for end in ends): raise ValueError("结束节点不能有出边")
for node in nodes:
    if node["kind"] == "decision" and len(outgoing[node["id"]]) != 2: raise ValueError("判断节点必须恰好有两条出边")
reachable = set(); pending = [starts[0]["id"]]
while pending:
    node_id = pending.pop()
    if node_id in reachable: continue
    reachable.add(node_id); pending.extend(outgoing[node_id])
if reachable != ids or not any(end["id"] in reachable for end in ends): raise ValueError("所有节点必须可从开始节点到达")
can_finish = set(); pending = [end["id"] for end in ends]
while pending:
    node_id = pending.pop()
    if node_id in can_finish: continue
    can_finish.add(node_id); pending.extend(incoming[node_id])
if can_finish != ids: raise ValueError("所有节点都必须能够到达结束节点")
def esc(value): return str(value).replace('\\', '\\\\').replace('"', '\\"').replace("\n", " ").replace("\r", " ")
shapes = {"start": "ellipse", "process": "box", "decision": "diamond", "end": "doublecircle"}
direction = data.get("direction", "TB")
if direction not in {"TB", "LR"}: raise ValueError("direction 只能是 TB 或 LR")
dot = [f'digraph G {{ rankdir={direction}; node [fontname="Noto Sans CJK SC"];']
for node in nodes: dot.append(f'"{esc(node["id"])}" [label="{esc(node.get("label", ""))}", shape={shapes.get(node.get("kind"), "box")}];')
for edge in edges: dot.append(f'"{esc(edge["source"])}" -> "{esc(edge["target"])}" [label="{esc(edge.get("label", ""))}"];')
dot.append("}")
subprocess.run(["dot", "-Tsvg", "-o", request["output"]], input="\n".join(dot), text=True, check=True, timeout=20)
print(json.dumps({"summary": f"已生成流程图，共 {len(nodes)} 个节点"}, ensure_ascii=False))
