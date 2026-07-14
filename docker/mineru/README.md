# MinerU 独立部署

本目录是可直接复制的完整部署单元：

```text
mineru/
├── .env
├── mineru.Dockerfile
├── mineru-compose.yaml
├── nginx.conf
└── README.md
```

MinerU 独立使用指定 NVIDIA GPU 完成文档拆页、OCR、版面理解和结果组装，不调用 GPUStack 中的聊天模型。

## 1. 检查主机

```bash
nvidia-smi
docker --version
docker compose version
docker run --rm --gpus all nvidia/cuda:12.9.1-base-ubuntu22.04 nvidia-smi
```

最后一条命令成功，说明 Docker 已能使用 NVIDIA GPU。

## 2. 配置

编辑本目录 `.env`：

```env
MINERU_GPU_DEVICE_ID=3
MINERU_GPU_MEMORY_UTILIZATION=0.7
MINERU_BIND_HOST=0.0.0.0
MINERU_PORT=30001
```

- `MINERU_GPU_DEVICE_ID`：`nvidia-smi` 中计划给 MinerU 使用的 GPU 编号。
- `MINERU_GPU_MEMORY_UTILIZATION`：占整块 GPU 显存的目标比例；共享 24GB GPU 时从 `0.7` 开始，独占时可提高到 `0.85`。
- `MINERU_BIND_HOST` 和 `MINERU_PORT`：Nginx 对外监听的固定地址和端口；MinerU 容器本身不再暴露宿主机端口。请让运维仅开放此端口，并限制调用方 IP。

建议让 MinerU 独占一块 GPU。若与 GPUStack 工作负载共享，需固定 GPUStack 调度，避免后续向该 GPU增加实例。

## 3. 构建并启动

```bash
docker compose -f mineru-compose.yaml up -d --build
```

首次构建会拉取 CUDA/vLLM 基础镜像、安装 MinerU，并从 ModelScope 下载模型。建议预留至少 30GB 磁盘。

## 4. 检查服务

```bash
docker compose -f mineru-compose.yaml ps
docker compose -f mineru-compose.yaml logs -f --tail 200 mineru-api
docker compose -f mineru-compose.yaml logs -f --tail 100 mineru-nginx
curl http://127.0.0.1:30001/health
```

## 5. 验证真实 PDF

先设置真实 PDF 的绝对路径：

```bash
PDF=/absolute/path/to/sample.pdf
```

再执行这一整行命令。`--form` 的参数与 Yuxi 调用 MinerU 的参数保持一致；不要拆行，避免 Shell 因续行符或空格导致参数失效：

```bash
curl --fail --silent --show-error --request POST 'http://127.0.0.1:30001/file_parse' --form "files=@${PDF};type=application/pdf" --form 'backend=hybrid-engine' --form 'parse_method=auto' --form 'effort=high' --form 'lang_list=ch' --form 'formula_enable=true' --form 'table_enable=true' --form 'image_analysis=true' --form 'return_md=true' --form 'return_images=true' --form 'response_format_zip=true' --output ./result.zip
```

解压后再查看 Markdown，不能单独移动或打开其中的 `.md` 文件：

```bash
mkdir -p result
unzip -oq result.zip -d result
find result -type f | sort
```

`return_images=true` 会把 MinerU 识别出的图片、图表和表格截图一并写入 ZIP；Markdown 使用这些相对路径。注意：它不会把 PDF 的每一页都转换为图片，只会导出被识别为视觉块的内容。

如果 Markdown 缺失正文，按以下顺序判断：

1. MinerU 会主动去除页眉、页脚、页码和部分脚注，这是其语义清理行为，不属于遗漏。
2. 对扫描件或 PDF 文本层异常的文件，将 `parse_method=auto` 改成 `parse_method=ocr` 后重新执行，强制 OCR。
3. 仍有缺失时，在命令末尾、`--output` 之前加入 `--form 'return_middle_json=true' --form 'return_content_list=true'`，重新解压结果并保留原 PDF、ZIP、MinerU 日志，用于检查内容是未识别还是被判为页眉/页脚等辅助内容。

## 6. 外部 Yuxi 接入

在 Yuxi 根目录 `.env` 中配置 MinerU 主机的内网 IP：

```env
MINERU_API_URI=http://<MinerU主机内网IP>:30001
MINERU_BACKEND=hybrid-engine
MINERU_TIMEOUT=1800
```

随后重新创建 Yuxi API 和 worker：

```bash
docker compose up -d --build api worker
```

在知识库解析配置中选择 `mineru_ocr`。

## 7. 固定端口转发

宿主机仅由 `mineru-nginx` 占用 `MINERU_PORT`（默认 `30001`），再转发到 Compose 内网的 `mineru-api:30001`，所以已有 `/health`、`/file_parse` 调用地址不变。

后续接入其他应用时，在 `nginx.conf` 增加更具体的路径，例如 `location /other-app/ { ... }`，然后执行：

```bash
docker compose -f mineru-compose.yaml up -d --force-recreate mineru-nginx
```

同一端口不能按“不同应用”自动分流，必须用不同路径或不同域名；不要让多个容器同时映射宿主机 `30001`。

## 8. 显存问题

- 启动 OOM：把 `MINERU_GPU_MEMORY_UTILIZATION` 从 `0.7` 降到 `0.6` 或 `0.5`。
- 运行后 OOM：检查 GPUStack 是否又向同一 GPU 调度了模型。
- 显存充足：可逐步提高到 `0.75`、`0.8`，修改后执行 `docker compose -f mineru-compose.yaml up -d --force-recreate`。
