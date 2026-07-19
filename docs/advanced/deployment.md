# 生产部署指南

本文档介绍如何在生产环境中部署 Yuxi。

## 前置要求

- Docker Engine (v24.0+)
- Docker Compose (v2.20+)
- NVIDIA Container Toolkit（如需使用 GPU 服务）

::: warning 注意事项
1. 生产环境和开发环境建议使用不同的机器，避免端口和资源冲突
2. 虽然名为「生产环境」，但这只是基本配置，真正上线需要根据实际情况调整
3. 前端有调试面板（长按侧边栏触发），生产环境建议关闭
:::

## 部署步骤

### 1. 准备配置文件

生产环境使用独立 `.env.prod` 文件。推荐通过管理脚本交互创建：

```bash
# Linux / macOS / Git Bash
bash scripts/manage.sh prod init

# Windows PowerShell
.\scripts\manage.ps1 prod init
```

脚本会复制 `.env.template`，自动生成并写入以下启动与安全必需项，且不会把密钥输出到终端：

- `JWT_SECRET_KEY`：随机生成至少 32 字节的密钥
- `YUXI_INSTANCE_ID`：为当前部署设置稳定且唯一的实例 ID
- `POSTGRES_PASSWORD`：修改默认密码
- `NEO4J_PASSWORD`：修改默认密码
- `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`：修改默认密钥

首次执行 `prod deploy` 时如果缺少 `.env.prod`，会自动进入同一交互流程。脚本不会覆盖已有 `.env.prod`；如需重新生成密码，应在确认尚未使用旧数据卷后手动修改对应值。

以下变量不影响容器启动，但应在部署前按实际环境手动添加或修改：

- `SILICONFLOW_API_KEY` 或其他模型提供商密钥：不配置时平台可启动，但无法正常调用模型
- `YUXI_CORS_ORIGINS`：浏览器跨域访问时必填
- `CHAT_IFRAME_*`：仅在需要限制 chat-iframe 自助换票来源或调整限流时配置
- 宿主机端口、GPU OCR、Sandbox、MinerU 等基础设施相关变量

仍可手动执行 `cp .env.template .env.prod`，但随后必须自行填写上面的安全变量；推荐优先使用 `prod init`。

chat-iframe 默认直接使用父页面传入的外部用户身份自助换票。如需限制可换票的宿主来源，可设置：

```env
CHAT_IFRAME_ALLOWED_ORIGINS=https://oa.example.com
```

生产 Compose 中存在 `${...}` 插值，因此启动时必须显式传入 `--env-file .env.prod`；仅配置服务级 `env_file` 不能替代 Compose 插值文件。

### 2. 启动服务

推荐使用项目统一管理脚本。`deploy` 会依次执行生产变量门禁、Compose 配置校验、镜像构建和后台启动：

```bash
# Linux / macOS / Git Bash：核心服务（CPU 模式）
bash scripts/manage.sh prod deploy

# Windows PowerShell：核心服务（CPU 模式）
.\scripts\manage.ps1 prod deploy

# 包含 GPU OCR 等 profile=all 服务
COMPOSE_PROFILES=all bash scripts/manage.sh prod deploy
$env:COMPOSE_PROFILES='all'; .\scripts\manage.ps1 prod deploy
```

管理脚本内部等价于以下 Compose 主流程：

```bash
# 可选：只解析环境变量、展开 Compose 配置并校验结构；不构建镜像、不启动或修改容器
docker compose --env-file .env.prod -f docker-compose.prod.yml config --quiet

# 启动核心服务（CPU 模式）
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build

# 启动所有服务（包含 GPU OCR）
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile all up -d --build
```

直接使用原生 Compose 命令时，`config --quiet` 是可选的发布前检查；统一管理脚本会固定执行它。其原理是让 Compose 先读取 `.env.prod`，完成 `${...}` 插值并验证最终配置模型；成功时不输出内容并返回退出码 `0`。`up` 内部也会执行配置解析，因此原生命令可以直接运行 `up`，但先检查能在镜像构建和容器变更前更快暴露配置错误。

### 3. 验证部署

- Web 访问：http://localhost（直接通过 80 端口）
- Chat Iframe：http://localhost/chat-iframe/
- Chat Iframe 父页面脚本：http://localhost/chat-iframe/docmind-chat-iframe-parent.js
- API 健康检查：`curl http://localhost/api/system/health`

## 维护与更新

统一命令格式为：

```text
bash scripts/manage.sh <dev|prod> <action> [service]
.\scripts\manage.ps1 <dev|prod> <action> [service]
```

| action | 作用 |
| --- | --- |
| `init` | 初始化环境文件；生产环境交互生成 `.env.prod` 的启动必需安全配置 |
| `deploy` | 校验配置、构建镜像并后台启动 |
| `start` | 不重新构建，启动已有服务 |
| `stop` | 停止服务但保留容器 |
| `restart` | 重启服务 |
| `down` | 删除容器和 Compose 网络，保留命名卷 |
| `status` | 查看服务状态 |
| `logs` | 跟随最近 200 行日志 |
| `build` | 只构建镜像 |
| `config` | 只校验环境变量和 Compose 最终配置 |

除 `down` 外，可在末尾指定单个服务，例如 `bash scripts/manage.sh prod logs api`。开发环境首次 `deploy` 且缺少 `.env` 时会复用 `scripts/init.sh` 或 `scripts/init.ps1` 初始化；生产环境首次 `deploy` 且缺少 `.env.prod` 时会交互生成独立配置，绝不会从开发配置复制。

API 镜像构建默认使用清华 Debian 源加速 apt 安装；如果本机 Docker 无法连接该镜像源，Dockerfile 会自动回退到 Debian 官方源。也可以在 `.env` 或 `.env.prod` 中显式切换：

```env
APT_MIRROR=deb.debian.org
APT_SECURITY_MIRROR=security.debian.org/debian-security
```

前端镜像构建默认通过 DaoCloud 代理解析 `node:24-alpine` 与 `nginx:alpine`，避免 Docker Hub 鉴权超时。内网已有镜像仓库或希望直连 Docker Hub 时，可覆盖：

```env
NODE_ALPINE_IMAGE=node:24-alpine
NGINX_ALPINE_IMAGE=nginx:alpine
```

### 更新代码

```bash
# 拉取最新代码
git pull

# 重新构建并启动
bash scripts/manage.sh prod deploy
```

### 查看日志

```bash
# API 日志
docker logs -f api-prod

# Nginx 访问日志
docker logs -f web-prod

# Chat Iframe 静态服务日志
docker logs -f chat-iframe-prod
```
