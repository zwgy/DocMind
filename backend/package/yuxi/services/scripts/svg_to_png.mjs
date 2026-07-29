import fs from "node:fs";
import { Resvg } from "@resvg/resvg-js";

const request = JSON.parse(fs.readFileSync(0, "utf8"));
if (!Number.isInteger(request.width) || request.width < 64 || request.width > 2400) {
  throw new Error("width 必须是 64 到 2400 之间的整数");
}

const svg = fs.readFileSync(request.source);
const renderer = new Resvg(svg, {
  fitTo: { mode: "width", value: request.width },
  font: {
    loadSystemFonts: true,
    defaultFontFamily: "Noto Sans CJK SC",
  },
});
fs.writeFileSync(request.output, renderer.render().asPng());
