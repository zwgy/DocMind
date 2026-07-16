# chat-iframe 部署说明

chat-iframe 支持两种部署方式：随 DocMind 一起部署，或单独构建镜像部署。两种方式共用 `docker/chat-iframe.Dockerfile` 和 `chat-iframe/nginx.conf`。

## 构建资源边界

| 环境 | 调试资源 | 说明 |
| --- | --- | --- |
| 开发环境 | 保留 `test1/`、`test2/`、`example.html`、`example-files/` | 用于本地联调和示例页面 |
| 生产环境 | 全部移除 | Docker 生产构建阶段清理，不进入最终 Nginx 镜像 |

## 方式一：随 DocMind 一起部署

### 开发环境

先按项目根目录 README 初始化 `.env`，然后启动：

```bash
# Linux / macOS / Git Bash
bash scripts/manage.sh dev deploy

# Windows PowerShell
.\scripts\manage.ps1 dev deploy
```

访问地址：

```text
DocMind Web: http://localhost:5173/
chat-iframe: http://localhost:5174/chat-iframe/
调试示例:   http://localhost:5174/chat-iframe/example.html
```

开发环境使用 Vite 热更新，chat-iframe 与主站分别占用 5174、5173 端口。

### 生产环境

先按 [生产部署指南](../docs/advanced/deployment.md) 交互创建 `.env.prod`：

```bash
# Linux / macOS / Git Bash
bash scripts/manage.sh prod init

# Windows PowerShell
.\scripts\manage.ps1 prod init
```

初始化会生成生产安全密码；模型密钥、跨域和自助换票等非启动必需项仍需按实际环境检查。随后执行：

```bash
# Linux / macOS / Git Bash
bash scripts/manage.sh prod deploy

# Windows PowerShell
.\scripts\manage.ps1 prod deploy
```

`deploy` 会先校验生产关键变量和 Compose 最终配置，再构建镜像并后台启动。只想提前检查时，可选执行 `bash scripts/manage.sh prod config` 或 `.\scripts\manage.ps1 prod config`；该操作不构建镜像，也不启动或修改容器。

生产环境仅由 `web-prod` 暴露 80 端口：

```text
DocMind Web:       http://localhost/
chat-iframe:       http://localhost/chat-iframe/
父页面集成脚本:   http://localhost/chat-iframe/docmind-chat-iframe-parent.js
```

主站 Nginx 将 `/chat-iframe/` 转发到 Compose 内网的 `chat-iframe-prod:80`。浏览器访问 `/api/` 时仍由主站转发到 `api:5050`。

常用管理命令：

```bash
bash scripts/manage.sh prod status
bash scripts/manage.sh prod logs chat-iframe
bash scripts/manage.sh prod restart chat-iframe
bash scripts/manage.sh prod stop chat-iframe
bash scripts/manage.sh prod start chat-iframe
```

验证：

```bash
curl -f http://localhost/api/system/health
curl -I http://localhost/chat-iframe/
curl -I http://localhost/chat-iframe/docmind-chat-iframe-parent.js
docker logs chat-iframe-prod --tail 100
docker logs web-prod --tail 100
```

生产镜像不应返回调试资源。Nginx 对不存在的前端路径会回退主入口，因此不能只依据 HTTP 状态码判断；以下检查不应匹配到调试页面正文：

```bash
! curl -fsS http://localhost/chat-iframe/example.html | grep -q 'docMind chat-iframe 嵌入示例'
```

`example-files/`、`test1/`、`test2/` 同样不得出现在最终容器的 `/usr/share/nginx/html/chat-iframe` 中。

## 方式二：单独部署 chat-iframe 镜像

chat-iframe 仍依赖 DocMind API。单独部署是指静态前端使用独立容器发布，不代表脱离 DocMind 后端运行。

构建生产镜像：

```bash
docker build -f docker/chat-iframe.Dockerfile --target production -t docmind-chat-iframe .
```

DocMind API 已通过 Compose 运行时，把容器接入同一个网络：

```bash
docker run -d --name docmind-chat-iframe \
  --network yuxi-know_app-network \
  -p 10002:80 \
  docmind-chat-iframe
```

访问与验证：

```text
http://localhost:10002/chat-iframe/
http://localhost:10002/chat-iframe/docmind-chat-iframe-parent.js
```

```bash
curl -I http://localhost:10002/chat-iframe/
curl -f http://localhost:10002/api/system/health
docker logs docmind-chat-iframe --tail 100
```

容器中的 `/api/` 默认转发到 `http://api:5050/api/`，所以目标 Docker 网络必须能解析服务名 `api`。如果 chat-iframe 与 DocMind API 位于不同主机，需要先把 `chat-iframe/nginx.conf` 中的 API 上游改为实际地址并重新构建镜像。

## 生产接入要求

- 父页面必须设置精确的 `targetOrigin` 和 `originAllowlist`，不要使用 `*`。
- 使用 `/api/chat-iframe/token` 自助换票时，必须同时配置 `CHAT_IFRAME_ALLOWED_SOURCES` 和 `CHAT_IFRAME_ALLOWED_ORIGINS`。
- 跨域宿主页面应显式设置 `apiBaseUrl`，或使用外部系统自己的 `tokenExchangeUrl`。
- 发布后使用真实父页面、测试业务身份和附件完成一次浏览器端问答验收。
