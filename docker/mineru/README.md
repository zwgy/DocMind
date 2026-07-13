# MinerU 独立部署

本目录是可直接复制的完整部署单元：

```text
mineru/
├── .env
├── mineru.Dockerfile
├── mineru-compose.yaml
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
- `MINERU_BIND_HOST`：允许外部电脑访问时使用 `0.0.0.0`，并通过内网防火墙只放行调用方 IP。

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
curl http://127.0.0.1:30001/health
```

## 5. 验证真实 PDF

```bash
curl -f -X POST http://127.0.0.1:30001/file_parse \
  -F "files=@sample.pdf" \
  -F "backend=hybrid-engine" \
  -F "return_md=true" \
  -F "response_format_zip=true" \
  -o result.zip
```

`result.zip` 能正常解压并包含 Markdown，说明 MinerU 解析链可用。

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

## 7. 显存问题

- 启动 OOM：把 `MINERU_GPU_MEMORY_UTILIZATION` 从 `0.7` 降到 `0.6` 或 `0.5`。
- 运行后 OOM：检查 GPUStack 是否又向同一 GPU 调度了模型。
- 显存充足：可逐步提高到 `0.75`、`0.8`，修改后执行 `docker compose -f mineru-compose.yaml up -d --force-recreate`。
