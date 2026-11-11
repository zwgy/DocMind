# 内网离线部署

采用同一镜像包部署测试和生产，配置与数据各自保存。目标机只需要 Linux x86_64、已运行的 Docker 引擎、Bash 和常用系统工具；不需要联网、Git、Python、Node 或构建工具。以下命令在 Linux 上执行。

## 1. 联网侧生成交付包

在有足够磁盘空间的联网构建机检出并提交确认后的 `main` 代码，使用不重复的发布标签：

```bash
bash scripts/package-offline.sh 2026.11.11-r1 /data/releases
```

脚本从当前提交的 Git 快照构建，不读取开发机 `.env` 或业务数据；已跟踪源码有未提交修改时拒绝打包。首次构建需下载依赖及基础镜像，时间和空间取决于缓存；需同时容纳构建缓存、运行镜像、导出目录和压缩包。构建中断留下的交付目录不算有效包，查明原因后使用新标签重试。

产物为 `docmind-offline-2026.11.11-r1.tar.gz` 及其 `.sha256` 文件。U 盘需支持大于 4 GB 的单文件，例如 exFAT；两份文件一起拷贝。

包内包含 11 类运行镜像（含动态沙盒）、固定版本 Compose v2.29.7 插件、最终 `compose.yaml`、部署入口、配置模板、镜像清单和校验文件。无需另存 Python/Node 等构建基础镜像。

内置 RapidOCR 使用文字检测、方向分类、文字识别三个 ONNX 模型，约 21 MB，构建时直接放入 API 镜像；同时放入 NLTK 分句数据。打包时在无网络的新容器中执行中文图片识别和分句检查。**安装人员无需另行下载 OCR 模型，也无需 GPU。** 大模型、嵌入模型通过内网 API 访问，不打包其权重。

## 2. 内网首次安装

安装前由运维确认根分区、`/data/2` 和 Docker 数据盘有可用空间。Docker 已安装不代表 Compose 插件已安装；出现 `docker: unknown command: docker compose` 时无需联网安装，交付入口会使用包内插件。

在 U 盘文件所在目录执行：

```bash
sha256sum -c docmind-offline-2026.11.11-r1.tar.gz.sha256
mkdir -p /data/2/docmind
tar -xzf docmind-offline-2026.11.11-r1.tar.gz \
  -C /data/2/docmind --strip-components=1
cd /data/2/docmind
bash deploy.sh init
```

`init` 交互生成 `.env.prod` 的独立随机密码，并在缺少 `docker/sandbox_provisioner/sandbox.env` 时从模板创建该文件；已有配置保持原样。初次可跳过 SiliconFlow 密钥，稍后配置实际内网供应商。编辑 `.env.prod`，至少核对：

| 配置 | 说明 |
| --- | --- |
| `COMPOSE_PROJECT_NAME=docmind` | 实例名；同机测试可用 `docmind-test`，生产可用 `docmind-prod` |
| `WEB_PORT=80` | 浏览器入口端口；同机测试可用 `8080`，不能与其他应用冲突 |
| `YUXI_CORS_ORIGINS`、`CHAT_IFRAME_ALLOWED_ORIGINS` | 按浏览器实际访问来源填写，使用 iframe 时填写其宿主系统来源 |
| 模型凭据环境变量 | 如使用环境变量方式，在此追加实际变量名与 Key；也可启动后在模型配置页填写 |

不要修改已经生成的数据库密码及实例标识。应用和数据库数据位于当前目录的 `docker/volumes/`；不要把另一环境的该目录或 `.env.prod` 拷来混用。`SANDBOX_DOCKER_THREADS_HOST_PATH` 通常留空自动检测；显式配置时必须是本实例 `docker/volumes/yuxi/threads` 的绝对路径。

```bash
bash deploy.sh install
bash deploy.sh status
```

`install` 校验所有交付文件后导入镜像，使用 `--no-build --pull never --wait` 启动。Compose 插件放在 `/data/2/docmind/.docker/cli-plugins/`，固定使用交付版本，不修改系统 Docker 或占用根分区的插件目录。该插件不自动加入系统命令；单独诊断时使用：

```bash
DOCKER_CONFIG=/data/2/docmind/.docker docker compose version
```

Docker 镜像层仍写到 `docker info` 中的 `DockerRootDir`；本目标机截图为 `/data/1/docker_data`。项目在 `/data/2` 不会自动改变该位置。业务数据库端口不向宿主机发布，服务间通过容器网络访问。

## 3. 模型配置与验收

浏览器打开 `http://目标机IP:WEB_PORT`，按首次启动页面完成管理员初始化；之后在“系统设置 → 模型配置”新增内网供应商，填写 Base URL、API Key、模型 ID，分别配置对话模型、嵌入模型和实际启用的重排模型。嵌入模型必须填写正确的向量维度。详见[模型配置](../intro/model-config.md)。

在系统文档解析设置中选择内置 `RapidOCR`，知识库上传使用系统默认解析配置。无需启用 MinerU/PaddleX，本包没有这两个 GPU 服务。状态就绪只证明进程/健康检查通过，测试环境必须再验证：

1. 用实际内网模型完成一次聊天。
2. 执行一次沙盒命令并打开生成文件，确认沙盒不在线拉取镜像或安装依赖。
3. 上传中文扫描 PDF，确认 OCR 文本、入库完成和检索命中。
4. 创建定时任务，确认到时执行及结果可查看。
5. 重启后确认账号、模型配置、知识库与任务数据保留。

本包不迁移开发环境账号、知识库、业务数据或自定义 Skills/MCP。需要的扩展须在联网侧准备依赖、在内网环境单独验证；公网搜索、云图片生成、在线插件安装及 GitHub 信息不能作为内网可用能力。部分品牌图标、头像仍引用公网资源，断网时可能缺图；不影响内网模型 API，但不能据此宣称完整界面已通过离线验收。

CentOS 7 的旧内核与现代镜像组合必须在目标机实际冷启动验证。联网开发机或本机 WSL 的结果不能替代目标机验证，也不能仅凭 Docker 版本保证兼容。

## 4. 日常操作与升级

```bash
bash deploy.sh logs api
bash deploy.sh restart
```

修改 `.env.prod` 后执行 `bash deploy.sh install` 使配置生效；`restart` 只重启，不更新容器配置。日志可能包含业务内容，分享前应检查并脱敏。

升级前停写并备份 `.env.prod`、`docker/sandbox_provisioner/sandbox.env` 和整个 `docker/volumes/`（数据库须使用一致性备份或停机复制），保留旧包。校验新版压缩包，在维护窗口解压至同一个部署目录并执行 `bash deploy.sh install`，不会覆盖已有的两份本机配置，也不会删除卷。数据库迁移后不能直接承诺旧镜像可回滚，恢复需与备份配套。

测试和生产使用同一发布包，同机并存时必须使用不同部署目录、`COMPOSE_PROJECT_NAME` 和 `WEB_PORT`。容器名、应用网络与动态沙盒前缀由项目名隔离。不要用 `docker compose down -v` 清理部署目录。
