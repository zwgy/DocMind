import fs from "node:fs";
import * as echarts from "echarts";
const request = JSON.parse(fs.readFileSync(0, "utf8"));
const lines = fs.readFileSync(request.source_path, "utf8").split(/\r?\n/).filter(Boolean);
const stack = []; let root = null; let count = 0; let maxDepth = 0; let maxLabelLength = 0;
for (const line of lines) { const match = line.match(/^( *)(- )(.+)$/); if (!match || match[1].length % 2) throw new Error("大纲只支持两个空格一级的无序列表"); const name = match[3].trim(); if (!name || name.length > 80) throw new Error("节点不能为空且不得超过 80 个字符"); const node = { name, children: [] }; const depth = match[1].length / 2; if (depth > 6) throw new Error("思维导图最多支持 7 层"); if (depth === 0) { if (root) throw new Error("思维导图只能有一个根节点"); root = node; } else { if (!stack[depth - 1]) throw new Error("大纲缩进层级不连续"); stack[depth - 1].children.push(node); } stack[depth] = node; stack.length = depth + 1; count++; maxDepth = Math.max(maxDepth, depth); maxLabelLength = Math.max(maxLabelLength, name.length); }
if (!root || count > 150) throw new Error("思维导图缺少根节点或节点过多");
const width = Math.min(4000, Math.max(1200, 500 + maxDepth * 230 + maxLabelLength * 14));
const height = Math.min(4000, Math.max(720, count * 42));
const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width, height });
chart.setOption({ animation: false, series: [{ type: "tree", data: [root], layout: request.layout === "radial" ? "radial" : "orthogonal", orient: request.layout === "radial" ? undefined : "LR", expandAndCollapse: false, label: { position: "left" } }] });
const svg = chart.renderToSVGString();
// SSR 进程必须释放图表实例，否则 Node 会保持事件循环而无法作为 CLI 返回。
chart.dispose();
fs.writeFileSync(request.output, svg);
console.log(JSON.stringify({ summary: `已生成思维导图，共 ${count} 个节点` }));
