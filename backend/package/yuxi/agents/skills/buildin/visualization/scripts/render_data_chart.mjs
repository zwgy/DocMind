import fs from "node:fs";
import { parse } from "csv-parse/sync";
import * as echarts from "echarts";

const request = JSON.parse(fs.readFileSync(0, "utf8"));
const fail = message => { throw new Error(message); };
const records = parse(fs.readFileSync(request.source_path, "utf8"), { bom: true, skip_empty_lines: true, relax_column_count: false });
const [columns, ...values] = records;
if (!Array.isArray(columns) || !columns.length) fail("CSV 必须包含表头");
if (new Set(columns).size !== columns.length || columns.some(value => !String(value).trim())) fail("CSV 表头不能为空且不能重复");
const rows = values.map(row => Object.fromEntries(columns.map((column, index) => [column, row[index]])));
if (!rows.length) fail("CSV 没有可用数据");
const has = field => field && columns.includes(field);
const encoding = request.encoding || {};
const number = (value, field) => {
  if (value === null || String(value).trim() === "") fail(`列 ${field} 存在空值`);
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) fail(`列 ${field} 必须全部是有限数值`);
  return parsed;
};
const assertDistinctFields = fields => {
  const selected = fields.filter(Boolean);
  if (new Set(selected).size !== selected.length) fail("字段映射不能重复使用同一列");
};
let option;
if (["bar", "line", "area"].includes(request.chart_type)) {
  if (!has(encoding.category) || !Array.isArray(encoding.values) || !encoding.values.length) fail("需要 category 和 values 字段映射");
  if (![encoding.category, ...encoding.values, encoding.series].filter(Boolean).every(has)) fail("字段映射包含不存在的列");
  assertDistinctFields([encoding.category, ...encoding.values, encoding.series]);
  if (encoding.series && encoding.values.length !== 1) fail("长表只能指定一个 values 字段");
  const categories = [...new Set(rows.map(row => String(row[encoding.category])))];
  if (categories.some(value => !value)) fail("分类列不能包含空值");
  let series;
  if (encoding.series) {
    const seriesNames = [...new Set(rows.map(row => String(row[encoding.series])))];
    if (seriesNames.some(value => !value)) fail("系列列不能包含空值");
    const field = encoding.values[0];
    series = seriesNames.map(name => {
      const points = new Map();
      for (const row of rows.filter(item => String(item[encoding.series]) === name)) {
        const category = String(row[encoding.category]);
        if (points.has(category)) fail("同一分类与系列组合不能重复");
        points.set(category, number(row[field], field));
      }
      return { name, type: request.chart_type === "bar" ? "bar" : "line", areaStyle: request.chart_type === "area" ? {} : undefined, data: categories.map(category => points.get(category) ?? null) };
    });
  } else {
    if (categories.length !== rows.length) fail("宽表的 category 必须唯一");
    series = encoding.values.map(field => ({ name: field, type: request.chart_type === "bar" ? "bar" : "line", areaStyle: request.chart_type === "area" ? {} : undefined, data: rows.map(row => number(row[field], field)) }));
  }
  option = { animation: false, title: { text: request.title }, tooltip: {}, legend: {}, xAxis: { type: "category", data: categories }, yAxis: { type: "value" }, series };
} else if (request.chart_type === "pie") {
  if (!has(encoding.name) || !has(encoding.value)) fail("饼图需要 name 和 value 字段映射");
  if (rows.length > 8 || new Set(rows.map(row => row[encoding.name])).size !== rows.length) fail("饼图分类必须唯一且不超过 8 个");
  assertDistinctFields([encoding.name, encoding.value]);
  option = { animation: false, title: { text: request.title }, tooltip: {}, series: [{ type: "pie", data: rows.map(row => { const value = number(row[encoding.value], encoding.value); if (value < 0) fail("饼图数值不能为负数"); return { name: String(row[encoding.name]), value }; }) }] };
} else if (request.chart_type === "scatter") {
  if (!has(encoding.x) || !has(encoding.y)) fail("散点图需要 x 和 y 字段映射");
  assertDistinctFields([encoding.x, encoding.y, encoding.label, encoding.size]);
  if (![encoding.x, encoding.y, encoding.label, encoding.size].filter(Boolean).every(has)) fail("字段映射包含不存在的列");
  const data = rows.map(row => ({
    value: [number(row[encoding.x], encoding.x), number(row[encoding.y], encoding.y)],
    name: encoding.label ? String(row[encoding.label]) : undefined,
    symbolSize: encoding.size ? number(row[encoding.size], encoding.size) : undefined,
  }));
  option = { animation: false, title: { text: request.title }, xAxis: {}, yAxis: {}, series: [{ type: "scatter", label: { show: Boolean(encoding.label), formatter: "{b}" }, data }] };
} else fail("不支持的图表类型");
if (rows.length > 2000) fail("数据点超过 2000 个限制");
const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: 1200, height: 720 });
chart.setOption(option);
fs.writeFileSync(request.output, chart.renderToSVGString());
console.log(JSON.stringify({ summary: `已生成${request.title}，共 ${rows.length} 个数据点` }));
