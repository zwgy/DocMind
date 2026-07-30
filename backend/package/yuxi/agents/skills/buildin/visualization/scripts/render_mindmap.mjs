import fs from "node:fs";
import { mindmap as mindmapLayout } from "@antv/hierarchy";
import * as echarts from "echarts";

const request = JSON.parse(fs.readFileSync(0, "utf8"));
if (typeof request.outline !== "string") throw new Error("outline 必须是 Markdown 无序列表大纲正文");
const lines = request.outline.split(/\r?\n/).filter(line => line.trim());
const stack = [];
let root = null;
let count = 0;
let maxDepth = 0;

for (const line of lines) {
  const match = line.match(/^( *)(- )(.+)$/);
  if (!match || match[1].length % 2) throw new Error("大纲只支持两个空格一级的无序列表");
  const name = match[3].trim();
  if (!name || name.length > 48) throw new Error("节点不能为空且不得超过 48 个字符");
  const node = { id: `node-${count + 1}`, name, children: [] };
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
}
if (!root || count > 100) throw new Error("思维导图缺少根节点或节点超过 100 个");

const branchPalette = [
  { stroke: "#2F6F5E", fill: "#E7F4EC", text: "#214E43" },
  { stroke: "#4F6F8F", fill: "#EAF0F6", text: "#304A63" },
  { stroke: "#B56B2D", fill: "#FFF1E2", text: "#754319" },
  { stroke: "#9B4D5B", fill: "#F8E9EC", text: "#6F3440" },
  { stroke: "#6B5B95", fill: "#F0ECF7", text: "#4C416B" },
  { stroke: "#4B7F86", fill: "#E8F2F3", text: "#31575C" },
];
const radial = request.layout === "radial";

function displayUnits(character) {
  // 中文全角字符的实际显示宽度约为 ASCII 的两倍，按字符数估算会让中英文
  // 标签的卡片宽度失真，因此换行和节点尺寸统一使用显示宽度。
  return /[\u2E80-\u9FFF\uF900-\uFAFF\uFF01-\uFF60\uFFE0-\uFFE6]/u.test(character) ? 2 : 1;
}

function lineDisplayUnits(value) {
  return Array.from(value).reduce((total, character) => total + displayUnits(character), 0);
}

function wrapLabel(value, lineLimit) {
  const wrapped = [];
  let line = "";
  let units = 0;
  for (const character of value) {
    const characterUnits = displayUnits(character);
    if (line && units + characterUnits > lineLimit) {
      wrapped.push(line.trimEnd());
      line = character.trimStart();
      units = line ? characterUnits : 0;
    } else {
      line += character;
      units += characterUnits;
    }
  }
  if (line) wrapped.push(line.trimEnd());
  return wrapped;
}

function prepareNode(node, depth, themeIndex) {
  const fontSize = depth === 0 ? 20 : depth === 1 ? 16 : depth === 2 ? 14 : 13;
  const lineHeight = depth === 0 ? 27 : depth === 1 ? 23 : 20;
  const horizontalPadding = depth === 0 ? 24 : depth === 1 ? 17 : 13;
  const verticalPadding = depth === 0 ? 14 : depth === 1 ? 11 : 8;
  const labelLines = wrapLabel(node.name, radial ? 18 : depth === 0 ? 26 : depth === 1 ? 22 : 24);
  const labelUnits = Math.max(...labelLines.map(lineDisplayUnits));
  node.depth = depth;
  node.themeIndex = themeIndex;
  node.label = labelLines.join("\n");
  node.fontSize = fontSize;
  node.lineHeight = lineHeight;
  node.width = Math.min(
    depth === 0 ? 320 : 280,
    Math.max(depth === 0 ? 160 : depth === 1 ? 110 : 84, labelUnits * fontSize * 0.55 + horizontalPadding * 2),
  );
  node.height = labelLines.length * lineHeight + verticalPadding * 2;
  node.weight = 0;
  node.children.forEach((child, index) => {
    prepareNode(child, depth + 1, depth === 0 ? index : themeIndex);
    node.weight += child.weight;
  });
  if (!node.children.length) node.weight = 1;
}

prepareNode(root, 0, 0);

// 左右分配按子树叶子数动态平衡，而不是简单按一级分支数量对半切分；
// 大小差异明显的分支因此不会全部堆在中心节点同一侧。
let leftWeight = 0;
let rightWeight = 0;
root.children.forEach((child, index) => {
  if (index === 0 || rightWeight <= leftWeight) {
    child.side = "right";
    rightWeight += child.weight;
  } else {
    child.side = "left";
    leftWeight += child.weight;
  }
});

const layoutRoot = mindmapLayout(root, {
  direction: radial ? "LR" : "H",
  radial,
  getId: data => data.id,
  getWidth: data => data.width,
  getHeight: data => data.height,
  getHGap: data => data.depth === 0 ? 44 : data.depth === 1 ? 34 : 26,
  getVGap: data => data.depth === 0 ? 26 : data.depth === 1 ? 17 : 12,
  getSubTreeSep: data => data.depth === 0 ? 30 : data.depth === 1 ? 22 : 10,
  getSide: child => child.data.side,
});

const layoutNodes = [];
layoutRoot.eachNode(node => layoutNodes.push(node));

function nodeRectangle(node) {
  if (radial) {
    // AntV 的 radial 变换把 x/y 改写为相对根节点的极坐标位置，此时坐标语义
    // 是节点中心而不是普通树布局的左上角。额外放大极坐标半径，为中文标签
    // 卡片预留环向间距，避免紧凑算法只按节点中心排布时发生边缘重叠。
    const radialExpansion = 2;
    return {
      x: node.x * radialExpansion - node.data.width / 2,
      y: node.y * radialExpansion - node.data.height / 2,
      width: node.data.width,
      height: node.data.height,
    };
  }
  return {
    x: node.x + node.hgap,
    y: node.y + node.vgap,
    width: node.data.width,
    height: node.data.height,
  };
}

let minX = Number.POSITIVE_INFINITY;
let minY = Number.POSITIVE_INFINITY;
let maxX = Number.NEGATIVE_INFINITY;
let maxY = Number.NEGATIVE_INFINITY;
for (const node of layoutNodes) {
  const rectangle = nodeRectangle(node);
  minX = Math.min(minX, rectangle.x);
  minY = Math.min(minY, rectangle.y);
  maxX = Math.max(maxX, rectangle.x + rectangle.width);
  maxY = Math.max(maxY, rectangle.y + rectangle.height);
}

const rawWidth = Math.max(1, maxX - minX);
const rawHeight = Math.max(1, maxY - minY);
const canvasPadding = 64;
const maximumCanvas = 4000;
// 极大导图必须整体等比收进 SVG 上限，否则简单截断画布会直接丢失分支。
const scale = Math.min(
  1,
  (maximumCanvas - canvasPadding * 2) / rawWidth,
  (maximumCanvas - canvasPadding * 2) / rawHeight,
);
const width = Math.ceil(
  Math.max(radial ? 980 : 1120, rawWidth * scale + canvasPadding * 2),
);
const height = Math.ceil(
  Math.max(radial ? 980 : 640, rawHeight * scale + canvasPadding * 2),
);
const offsetX = (width - rawWidth * scale) / 2 - minX * scale;
const offsetY = (height - rawHeight * scale) / 2 - minY * scale;

function transformRectangle(rectangle) {
  return {
    x: rectangle.x * scale + offsetX,
    y: rectangle.y * scale + offsetY,
    width: rectangle.width * scale,
    height: rectangle.height * scale,
  };
}

function boundaryPoint(rectangle, towardX, towardY) {
  const centerX = rectangle.x + rectangle.width / 2;
  const centerY = rectangle.y + rectangle.height / 2;
  const deltaX = towardX - centerX;
  const deltaY = towardY - centerY;
  const ratio = 1 / Math.max(
    Math.abs(deltaX) / Math.max(rectangle.width / 2, 1),
    Math.abs(deltaY) / Math.max(rectangle.height / 2, 1),
    1,
  );
  return [centerX + deltaX * ratio, centerY + deltaY * ratio];
}

function branchTheme(node) {
  return branchPalette[node.data.themeIndex % branchPalette.length];
}

const graphics = [];
for (const node of layoutNodes) {
  if (!node.parent) continue;
  const parentRectangle = transformRectangle(nodeRectangle(node.parent));
  const childRectangle = transformRectangle(nodeRectangle(node));
  const parentCenter = [
    parentRectangle.x + parentRectangle.width / 2,
    parentRectangle.y + parentRectangle.height / 2,
  ];
  const childCenter = [
    childRectangle.x + childRectangle.width / 2,
    childRectangle.y + childRectangle.height / 2,
  ];
  const start = boundaryPoint(parentRectangle, childCenter[0], childCenter[1]);
  const end = boundaryPoint(childRectangle, parentCenter[0], parentCenter[1]);
  const middleX = start[0] + (end[0] - start[0]) * 0.5;
  graphics.push({
    type: "bezierCurve",
    silent: true,
    z: 1,
    shape: {
      x1: start[0],
      y1: start[1],
      x2: end[0],
      y2: end[1],
      cpx1: middleX,
      cpy1: start[1],
      cpx2: middleX,
      cpy2: end[1],
    },
    style: {
      stroke: branchTheme(node).stroke,
      fill: null,
      lineWidth: Math.max(1, (node.depth === 1 ? 3 : 1.8) * scale),
      opacity: Math.max(0.48, 0.92 - node.depth * 0.08),
    },
  });
}

for (const node of layoutNodes) {
  const rectangle = transformRectangle(nodeRectangle(node));
  const theme = branchTheme(node);
  const isRoot = node.depth === 0;
  const isPrimaryBranch = node.depth === 1;
  graphics.push({
    type: "group",
    silent: true,
    z: isRoot ? 4 : 3,
    children: [
      {
        type: "rect",
        shape: {
          x: rectangle.x,
          y: rectangle.y,
          width: rectangle.width,
          height: rectangle.height,
          r: Math.max(4, (isRoot ? 8 : 6) * scale),
        },
        style: {
          fill: isRoot ? "#263746" : isPrimaryBranch ? theme.stroke : theme.fill,
          stroke: isRoot ? "#263746" : theme.stroke,
          lineWidth: Math.max(1, (isRoot || isPrimaryBranch ? 2 : 1.2) * scale),
        },
      },
      {
        type: "text",
        style: {
          x: rectangle.x + rectangle.width / 2,
          y: rectangle.y + rectangle.height / 2,
          text: node.data.label,
          fill: isRoot || isPrimaryBranch ? "#FFFFFF" : theme.text,
          font: `${isRoot || isPrimaryBranch ? 600 : 400} ${Math.max(9, node.data.fontSize * scale)}px Noto Sans CJK SC, sans-serif`,
          lineHeight: Math.max(12, node.data.lineHeight * scale),
          align: "center",
          verticalAlign: "middle",
        },
      },
    ],
  });
}

const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width, height });
chart.setOption({
  animation: false,
  backgroundColor: "#FFFFFF",
  graphic: graphics,
});
const svg = chart.renderToSVGString();
// SSR 进程必须释放图表实例，否则 Node 会保持事件循环而无法作为 CLI 返回。
chart.dispose();
fs.writeFileSync(request.output, svg);
console.log(JSON.stringify({
  summary: `已生成${radial ? "径向" : "双向"}思维导图，共 ${count} 个节点、${maxDepth + 1} 层`,
}));
