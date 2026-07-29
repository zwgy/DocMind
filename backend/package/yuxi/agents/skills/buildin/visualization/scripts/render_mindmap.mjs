import fs from "node:fs";
import * as echarts from "echarts";

const request = JSON.parse(fs.readFileSync(0, "utf8"));
const lines = fs.readFileSync(request.source_path, "utf8").split(/\r?\n/).filter(Boolean);
const stack = [];
let root = null;
let count = 0;
let leaves = 0;
let maxDepth = 0;
let maxLabelLength = 0;

for (const line of lines) {
  const match = line.match(/^( *)(- )(.+)$/);
  if (!match || match[1].length % 2) throw new Error("大纲只支持两个空格一级的无序列表");
  const name = match[3].trim();
  if (!name || name.length > 80) throw new Error("节点不能为空且不得超过 80 个字符");
  const node = { name, children: [] };
  const depth = match[1].length / 2;
  if (depth > 6) throw new Error("思维导图最多支持 7 层");
  if (depth === 0) {
    if (root) throw new Error("思维导图只能有一个根节点");
    root = node;
  } else {
    if (!stack[depth - 1]) throw new Error("大纲缩进层级不连续");
    stack[depth - 1].children.push(node);
  }
  stack[depth] = node;
  stack.length = depth + 1;
  count++;
  maxDepth = Math.max(maxDepth, depth);
  maxLabelLength = Math.max(maxLabelLength, name.length);
}
if (!root || count > 150) throw new Error("思维导图缺少根节点或节点过多");

const branchPalette = [
  { stroke: "#2F6F5E", fill: "#E7F4EC", text: "#214E43" },
  { stroke: "#4F6F8F", fill: "#EAF0F6", text: "#304A63" },
  { stroke: "#B56B2D", fill: "#FFF1E2", text: "#754319" },
  { stroke: "#9B4D5B", fill: "#F8E9EC", text: "#6F3440" },
  { stroke: "#6B5B95", fill: "#F0ECF7", text: "#4C416B" },
  { stroke: "#4B7F86", fill: "#E8F2F3", text: "#31575C" },
];
const radial = request.layout === "radial";

function themeBranch(node, theme, depth) {
  node.symbol = "circle";
  node.symbolSize = depth === 1 ? 12 : Math.max(6, 10 - depth);
  node.itemStyle = { color: depth === 1 ? theme.stroke : "#FFFFFF", borderColor: theme.stroke, borderWidth: 2 };
  node.lineStyle = { color: theme.stroke, width: depth === 1 ? 2.4 : 1.5, opacity: Math.max(0.42, 0.85 - depth * 0.08) };
  node.label = {
    position: radial ? "top" : "right",
    distance: depth === 1 ? 10 : 7,
    color: depth === 1 ? "#FFFFFF" : theme.text,
    backgroundColor: depth === 1 ? theme.stroke : theme.fill,
    borderColor: theme.stroke,
    borderWidth: depth === 1 ? 0 : 1,
    borderRadius: 4,
    padding: depth === 1 ? [6, 9] : [4, 7],
    fontSize: depth === 1 ? 14 : 12,
    fontWeight: depth === 1 ? 600 : 400,
  };
  if (!node.children.length) leaves++;
  node.children.forEach(child => themeBranch(child, theme, depth + 1));
}

root.symbol = "roundRect";
root.symbolSize = [24, 24];
root.itemStyle = { color: "#263746", borderColor: "#263746", borderWidth: 2 };
root.label = {
  position: radial ? "top" : "left",
  distance: 12,
  color: "#FFFFFF",
  backgroundColor: "#263746",
  borderRadius: 4,
  padding: [8, 12],
  fontSize: 17,
  fontWeight: 600,
};
root.children.forEach((branch, index) => themeBranch(branch, branchPalette[index % branchPalette.length], 1));

const width = Math.min(4000, Math.max(1280, 520 + maxDepth * 260 + maxLabelLength * 13));
const height = radial
  ? Math.min(4000, Math.max(900, Math.ceil(Math.sqrt(count)) * 170))
  : Math.min(4000, Math.max(760, Math.max(leaves, 1) * 64 + 180));
const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width, height });
chart.setOption({
  animation: false,
  backgroundColor: "#FFFFFF",
  textStyle: { fontFamily: "Noto Sans CJK SC, sans-serif" },
  series: [{
    type: "tree",
    data: [root],
    layout: radial ? "radial" : "orthogonal",
    orient: radial ? undefined : "LR",
    top: radial ? "10%" : 70,
    bottom: radial ? "10%" : 70,
    left: radial ? "10%" : 150,
    right: radial ? "10%" : 220,
    symbol: "circle",
    edgeShape: radial ? "curve" : "polyline",
    edgeForkPosition: "62%",
    initialTreeDepth: -1,
    expandAndCollapse: false,
    roam: false,
    lineStyle: { color: "#AAB5C0", width: 1.5, curveness: radial ? 0.45 : 0.2 },
    emphasis: { disabled: true },
  }],
});
const svg = chart.renderToSVGString();
// SSR 进程必须释放图表实例，否则 Node 会保持事件循环而无法作为 CLI 返回。
chart.dispose();
fs.writeFileSync(request.output, svg);
console.log(JSON.stringify({ summary: `已生成思维导图，共 ${count} 个节点` }));
