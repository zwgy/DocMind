# 配置系统详解

## 概述

系统采用多层配置架构，模型配置由网页界面管理，应用配置基于 Pydantic + TOML。

## 先分清配置写在哪里

项目没有一条适用于所有配置的“环境变量覆盖网页配置”规则。按用途使用下面的入口：

| 配置 | 入口 | 如何生效 |
| --- | --- | --- |
| 进程连接、密钥、网页来源 | 开发 `.env`；生产 `.env.prod` | Compose 把变量传给容器；修改后重新创建服务 |
| 模型供应商、地址、模型 ID、向量维度 | 系统设置 → 模型配置 | 保存至数据库；凭据可以直接填写，也可引用环境变量名 |
| 默认模型、文档解析 OCR、来文处理设置 | 系统设置 | 保存至 `saves/config/base.toml`，再同步 Redis 和各后台进程 |
| 沙盒工具使用的模型、工具凭据 | `docker/sandbox_provisioner/sandbox.env` | 与后台环境文件分开，按沙盒说明重建相应实例 |
| 镜像软件源、网页构建地址 | 联网构建侧 `.env` 或按需追加至 `.env.prod` | 重新构建镜像或静态网页；内网运行时修改无效 |

`Config` 使用 Pydantic `BaseModel`，不会把任意同名环境变量自动映射到网页配置字段。

## 环境文件怎么生成

```text
开发：.env.template       → scripts/init.sh 或 init.ps1 → .env
生产：.env.prod.template  → scripts/manage.sh prod init → .env.prod
离线：包内 .env.prod.template → deploy.sh init          → .env.prod
```

Windows 生产入口为 `scripts/manage.ps1 prod init`。生产只询问是否生成配置，不强制任何云供应商密钥。已有 `.env.prod` 原样保留，不覆盖密码，也不会自动删掉旧文件中的多余行。

离线模板在生产模板上增加 `COMPOSE_PROJECT_NAME=docmind`、`WEB_PORT=80`。这两个入口只针对离线生成的 `compose.yaml`：源码生产编排仍固定网页 80 端口和容器名称，不能只修改这两个值就宣称完成多实例隔离。

### 必须保留，但不需要人工编密码

生产初始化会生成 `JWT_SECRET_KEY`、`YUXI_INSTANCE_ID`、`SANDBOX_PROVISIONER_TOKEN`、`POSTGRES_PASSWORD`、`NEO4J_PASSWORD`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`。Linux 配置文件只允许当前用户读取。已有数据库首次启动后，不要随意替换密码；同时保存好配置和数据备份。

数据库内部地址已经由 Compose 配好，默认连接本实例的 PostgreSQL、Redis、Neo4j、Milvus 和 MinIO，不需要填写服务器 IP。外部数据库接入要同时处理连接配置与包内服务，不属于首次标准部署的必填步骤。

### 需要确认的网页入口

| 参数 | 怎么填 | 留空的含义 |
| --- | --- | --- |
| `YUXI_CORS_ORIGINS` | 发起跨域调用的网页地址，英文逗号分隔；支持 `*`，但此时不携带 Cookie | 生产不开放跨域，正常同源访问不受影响 |
| `CHAT_IFRAME_ALLOWED_ORIGINS` | 发起外部用户换票的网页地址，英文逗号分隔；**不支持 `*` 通配符** | 不检查换票来源，不是关闭换票功能 |
| `CHAT_IFRAME_TOKEN_RATE_LIMIT_PER_MINUTE` | 按需取消注释，默认每 IP 每分钟 60 次；0 表示关闭这项限流 | 使用默认值 |

网页地址只写协议、主机和端口，不加页面路径或末尾 `/`。例如业务系统网页为 `http://10.0.0.20:8080`，就填写该地址；不要误填模型服务地址。

### 按需配置的项默认注释

- **模型凭据**：在供应商中直接填 API Key，或设置凭据变量名并在环境文件中新增同名变量，例如 `INTRANET_MODEL_API_KEY`。任意自定义变量名都可以，不限于模板中的示例。内网自建模型不需要填写 `SILICONFLOW_API_KEY`。
- **沙盒调节**：模板只列超时、输出大小、闲置等待和代理等常用项。默认管理服务和容器内部地址已经配置，不需要填写外部沙盒地址。其他后端、卷挂载和高级参数见[沙盒说明](../agents/sandbox-architecture.md)。
- **URL 文档解析**：`YUXI_URL_WHITELIST` 填允许解析的域名/IP，空值关闭 URL 解析；它与浏览器来源限制是不同功能。
- **公网工具与额外 OCR**：需要 Tavily 搜索或 MinerU 等功能时，再在联网部署侧填写相应参数。标准内网包使用内置 RapidOCR，不包含这些额外服务，不在生产运行模板中列出。

### 只在开发或构建时使用的项

`.env.template` 保留开发端口和网页控制台地址，避免把已约定的 6380、9010、9011、19532、9092 等端口改回 Compose 的其他默认值。生产离线编排通过内部网络连接基础服务，不开放这些开发端口。

`APT_MIRROR`、`APT_SECURITY_MIRROR` 是镜像构建时下载系统软件的地址；`NODE_ALPINE_IMAGE`、`NGINX_ALPINE_IMAGE` 是构建使用的基础镜像。`VITE_MINIO_CONSOLE_URL`、`VITE_MILVUS_WEBUI_URL` 在网页构建时写入产物。它们不属于离线机器的运行配置；内网使用已打好的镜像和网页，不会因缺少这些变量而下载软件。

### 已移除的模板入口

`INCOMING_OCR_ENGINE`、`INCOMING_OCR_ENGINE_CONFIG_JSON`、`BUSINESS_EXTRACTION_MODEL`、`YUXI_SUPER_ADMIN_NAME`、`YUXI_SUPER_ADMIN_PASSWORD` 已不作为当前配置入口。OCR 改为系统设置 → 文档解析配置，选择 **RapidOCR (ONNX)**，高级参数通常保留 `{}`；默认仍是不启用，首次部署要明确保存。管理员通过网页首次初始化创建。

`OPENAI_API_BASE` 不作为统一模型地址入口，地址在供应商设置中填写。`ARK_API_KEY`、`TOGETHER_API_KEY` 不再作为内置供应商示例；已有自定义供应商若使用同名变量，继续保留实际值。

清理已有文件时，只删除确认失效的配置；不要重新生成覆盖，也不要删除自定义供应商正在引用的变量。

## 模型配置

由网页统一管理，详见 [模型配置](../intro/model-config.md)。

## 应用配置

配置项定义于 `backend/package/yuxi/config/app.py`，用户修改保存至 `saves/config/base.toml`。

### 修改配置

```python
from yuxi.config import config

config.default_model = "provider-id:model-id"
config.save()
```

配置会在保存 `base.toml` 后写入 Redis 快照（`yuxi:runtime_config`）。快照包含可运行时同步的公开配置字段，不包含 `_` 开头的内部属性和 `save_dir`；API/worker 进程在启动时各拉起一个后台同步线程，按 5 秒间隔从该快照刷新内存值，读取端无需感知。Redis 不可用时继续使用当前内存值。

`save_dir` 是启动期内部路径配置，不在管理员配置中展示，也不支持通过管理员配置接口、`base.toml` 或运行时 Redis 快照修改。sandbox 相关配置仍属于启动期敏感配置，运行中的已初始化组件不承诺完整热更新，修改后需要重启服务保证生效。

## 常见问题

**配置文件损坏**：删除 `saves/config/base.toml`，系统将重新生成默认配置。
