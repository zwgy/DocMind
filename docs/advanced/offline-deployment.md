# 内网离线部署

首次交付三个镜像包和一个应用包，后续通常只交付应用包。测试与生产使用同一套文件，各自保存配置和数据。目标机只需 Linux x86_64、已运行的 Docker、Bash 和常用系统工具，无需联网、Git、Python 或 Node。

## 1. 每个包包含什么

以下以首次环境版本 `2026.11.12-r2` 为例。镜像包是 Docker 导出文件，直接导入，不要将其当作应用包解压。

| 文件 | 内容 | 何时需要更新 |
| --- | --- | --- |
| `docmind-images-infra-2026.11.12-r2.tar.gz` | 六类基础镜像：PostgreSQL、Redis、Neo4j、Milvus、etcd、MinIO | 首次安装，或基础服务升级 |
| `docmind-images-app-2026.11.12-r2.tar.gz` | 三类运行镜像：API（同时用于六个后台进程）、Web 的 Nginx、iframe 的 Nginx；API 内含 Python 依赖、系统工具、内置 OCR 资源 | 首次安装，或后台依赖、系统工具、运行镜像发生变化 |
| `docmind-images-sandbox-2026.11.12-r2.tar.gz` | 沙盒执行环境和沙盒管理服务两类镜像 | 首次安装，或沙盒运行环境变化 |
| `docmind-app-2026.11.12-r2.tar.gz` | 后台源码、沙盒管理源码、构建好的两套网页、Nginx 配置、编排和部署脚本；首次应用包还包含 Compose v2.29.7 插件 | 日常功能修改时 |

共 11 类镜像，未更换或精简沙盒、API 依赖。镜像内原有代码仍保留，但实际运行读取外部挂载文件，普通更新不再导出这些大镜像。首次总传输量不会因为拆包自动减少；拆包便于分别传输，后续只传有变化的文件。

内置 RapidOCR 的三个 ONNX 模型约 21 MB，和 NLTK 分句数据一起预置于 API 镜像；完整打包时在断网新容器中验证。无需现场另装 OCR 模型或 GPU。大模型、嵌入模型通过内网 Base URL、API Key 调用，不打包其权重。

## 2. 联网侧打包

在联网 Linux x86_64 构建机检出、提交确认后的 main，选择未使用的标签：

```bash
bash scripts/package-offline.sh 2026.11.12-r2 /data/releases
```

输出目录为 `/data/releases/docmind-offline-2026.11.12-r2/`，含上述四包、`SHA256SUMS`、部署说明和源码提交记录。脚本仅允许从 main 的 Git 提交快照构建，不读取开发机配置和业务数据。需预留运行镜像、构建缓存和导出包所需空间；失败目录不算有效交付，请修复原因后换标签重试。

联网构建机同时保留一份解开的首次应用包，用于后续识别固定环境版本：

```bash
mkdir -p /data/releases/docmind-runtime-r2
tar -xzf /data/releases/docmind-offline-2026.11.12-r2/docmind-app-2026.11.12-r2.tar.gz \
  -C /data/releases/docmind-runtime-r2
```

不要把构建机的 Docker 镜像、上述环境记录目录清掉后，误以为仍能直接生成代码包。

## 3. 内网首次安装

在 U 盘交付目录校验并复制四个包：

```bash
sha256sum -c SHA256SUMS
mkdir -p /data/2/docmind
cp docmind-app-*.tar.gz docmind-images-*.tar.gz /data/2/docmind/
cd /data/2/docmind
tar -xzf docmind-app-2026.11.12-r2.tar.gz
bash deploy.sh init
```

init 创建独立随机密码及配置；已有配置保持原样。编辑 `.env.prod`，核对：

| 配置 | 说明 |
| --- | --- |
| `COMPOSE_PROJECT_NAME=docmind` | 同机测试与生产必须不同，如 docmind-test、docmind-prod |
| `WEB_PORT=80` | 浏览器入口端口，同机其他环境不能重复 |
| `YUXI_CORS_ORIGINS`、`CHAT_IFRAME_ALLOWED_ORIGINS` | 按浏览器和 iframe 宿主系统的实际访问来源填写 |
| 实际使用的模型凭据变量 | 可以在此配置，也可启动后在模型设置页填写 |

不要修改已初始化使用的数据库密码和实例标识。执行：

```bash
bash deploy.sh install
bash deploy.sh status
```

install 校验应用文件和三个镜像包，再分别导入镜像，禁止在线拉取或现场构建。没有 docker compose 的机器会自动使用首次应用包内的插件，插件位于 `/data/2/docmind/.docker/cli-plugins/`，不修改系统目录。单独查看版本可执行：

```bash
DOCKER_CONFIG=/data/2/docmind/.docker docker compose version
```

业务数据继续位于 `docker/volumes/`，本机配置为 `.env.prod` 和 `docker/sandbox_provisioner/sandbox.env`。Docker 镜像仍存于 DockerRootDir，本目标机为 `/data/1/docker_data`；项目放到 /data/2 不会改变镜像存储位置。

目录关系：

```text
/data/2/docmind/
  app/backend/package/       后台业务源码（六个后台进程共用）
  app/backend/server/        API、来文和 Worker 入口源码
  app/web/dist/              构建好的主网页
  app/chat-iframe/dist/      构建好的 iframe 网页
  app/docker/                Nginx 配置、沙盒管理服务源码
  .env.prod                  本机配置，更新不覆盖
  docker/volumes/            业务数据，更新不删除
  deploy.sh                  统一操作入口
```

## 4. 日常只更新代码

联网侧提交新代码后，复用原有环境记录生成应用包：

```bash
bash scripts/package-offline.sh --code-only 2026.11.13-code1 \
  /data/releases/docmind-runtime-r2 /data/releases
```

产物在 `docmind-offline-2026.11.13-code1/`，仅有新应用包、校验及说明文件。脚本在构建侧编译前端、提取静态文件，不构建或导出 API、沙盒、数据库等运行镜像，代码包也不重复携带 Compose 插件。

把新应用包和该目录的 SHA256SUMS 复制到内网临时交付目录，校验后执行：

```bash
cd /data/2/delivery-code1
sha256sum -c SHA256SUMS
cd /data/2/docmind
bash deploy.sh update /data/2/delivery-code1/docmind-app-2026.11.13-code1.tar.gz
```

无需手动覆盖正在运行的代码。update 先检查应用包、环境版本和编排配置，再停止九个应用服务、整体替换 app 目录、重新创建应用容器。删除的旧代码不会残留；数据库等基础服务不重启，不导入镜像，不覆盖本机配置和业务数据。更新期间应用会短暂不可用，请选择维护窗口，并等当前任务结束。

更新前保留上一个应用包。新代码启动或健康检查失败时，命令返回失败，应用可能暂停；没有自动回滚。修复后重试，或执行同一条 update 命令重新应用上一个应用包。该方式适用于未迁移数据库且运行环境相同的代码更新。

前端不能直接把 Vue 源码拷进内网使用，必须由打包脚本先构建。后端普通 Python 代码可通过挂载生效；使用统一应用包更新更容易保持六个进程、两套网页版本一致。若人工修改挂载文件，重启对应服务后才算完成更新。

后台依赖、系统工具或运行镜像构建输入变化时，脚本明确要求重新生成完整分包。前端依赖变化只需重新构建静态产物。涉及数据库结构变化时，须按该版本迁移要求处理，不能将“无需更新镜像”等同于“无需迁移数据库”。

## 5. 配置修改、环境升级与验收

```bash
bash deploy.sh logs api
bash deploy.sh restart
# 修改 .env.prod 后使新配置生效，不重复导入镜像：
bash deploy.sh apply
```

运行环境升级时，先备份本机配置和业务数据（数据库需一致性备份或停机复制），保留旧应用包。在维护窗口执行 `bash deploy.sh stop`，将新四包放到部署目录、解开新的首次应用包，再执行 install。不要覆盖现有 .env.prod 和 sandbox.env，也不要删除 docker/volumes。数据库迁移后的回退必须与备份配套，不承诺一键自动回滚。

首次启动用浏览器完成管理员初始化，在“系统设置 → 模型配置”填写内网供应商、Base URL、API Key、模型 ID 和嵌入维度；文档解析选择内置 RapidOCR。本包不启动 MinerU/PaddleX，不迁移开发机账号、业务数据、自定义 Skills/MCP。

测试环境至少验收：实际内网模型聊天、沙盒命令及文件、中文扫描 PDF 的 OCR/入库/检索、定时任务、重启后账号与业务数据保留。代码更新还应确认页面和后台确实使用新文件。健康检查通过只代表进程就绪。

CentOS 7 旧内核需在内网目标机实际冷启动验收，远程开发机验证不能替代它。公网搜索、在线插件安装、云端图片生成和公网图标等外网资源也不属于内网模型可访问的范围。不要使用 docker compose down -v 清理业务环境。
