# 网页内置品牌图标

本目录仅收录模型与知识库界面实际使用的图标，随 Vite 构建复制到网页中，运行时无需访问外网。

- 来源：LobeHub，https://github.com/lobehub/lobe-icons
- 固定资源版本：`@lobehub/icons-static-svg@1.102.0`
- 下载包：https://registry.npmjs.org/@lobehub/icons-static-svg/-/icons-static-svg-1.102.0.tgz
- 许可：MIT，原文见同目录 `LICENSE`；品牌名称和标志的权利归各自所有者。
- `default.svg` 是本项目的通用模型图标。

新增模型图标时，把对应 SVG 放入此目录并更新 `web/src/utils/modelIcon.js`，不要恢复外网图片地址。
