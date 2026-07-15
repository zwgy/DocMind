# chat-iframe 双模式 Docker 部署实施方案

日期：2026-07-15

## 目标

让 `chat-iframe` 保持独立镜像边界，同时支持：

1. 开发环境随 `docker compose up` 启动，并通过独立 Vite 端口热更新。
2. 生产环境随 `docker-compose.prod.yml` 启动，通过 `web-prod` 的 `/chat-iframe/` 统一入口访问。
3. 使用同一个生产镜像接入已有 docMind Docker 网络后独立部署。

## 实施范围

- 新增 `docker/chat-iframe.Dockerfile`，包含 `development`、`build-stage`、`production` 三个阶段。
- 移除旧 `chat-iframe/Dockerfile`，避免维护重复入口。
- 开发 Compose 增加 `chat-iframe-dev`，默认映射宿主机 5174 端口。
- 生产 Compose 增加仅在 `app-network` 暴露 80 端口的 `chat-iframe-prod`。
- 主站现有 `docker/nginx/default.conf` 增加 `/chat-iframe/` 反向代理。
- 保留 `chat-iframe/nginx.conf` 作为独立静态服务配置，不新增 Nginx 配置文件。
- 根 `.dockerignore` 排除 chat-iframe 本地依赖、构建产物和测试来文。

## 运行链路

```text
开发：浏览器 -> 5174 -> chat-iframe Vite -> api:5050

生产：浏览器 -> web-prod:80/chat-iframe/
                 -> chat-iframe-prod:80
      浏览器 -> web-prod:80/api/ -> api:5050

独立：浏览器 -> 独立映射端口 -> chat-iframe-prod:80 -> api:5050
```

## 验收标准

- `docker compose up -d --build` 后可访问 `http://localhost:5174/chat-iframe/`，源码修改可热更新。
- `docker compose -f docker-compose.prod.yml up -d --build` 后可访问 `http://localhost/chat-iframe/`，宿主机不额外开放 chat-iframe 端口。
- `/chat-iframe/docmind-chat-iframe-parent.js` 和构建后的 assets 可通过主站 80 端口访问。
- chat-iframe 内部 `/api/` 请求和 SSE 流式响应正常。
- 独立镜像可以通过 `docker/chat-iframe.Dockerfile` 构建，并在接入 docMind Docker 网络后运行。
- 生产构建上下文不包含 `node_modules`、`dist`、`public/test1` 和 `public/test2`。

## Checklist

- [x] 新增统一多阶段 Dockerfile。
- [x] 接入开发 Compose。
- [x] 接入生产 Compose 与主站 Nginx 路由。
- [x] 同步部署文档与 changelog。
- [x] `corepack pnpm test:p1` 通过（97 通过、3 跳过），包含类型检查、Lint 和生产构建。
- [x] 两个 Compose 通过 YAML 解析及开发/生产关键拓扑断言。
- [ ] Docker 中完成开发、生产和独立镜像验证。
- [ ] 完成真实浏览器嵌入与 SSE 主链路 E2E。
