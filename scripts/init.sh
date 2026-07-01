#!/bin/bash

# Yuxi Initialization Script for Bash/Linux/macOS
# This script helps set up the environment for the Yuxi project

set -e

generate_hex() {
    local length="$1"
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex "$length"
    else
        tr -dc 'a-f0-9' < /dev/urandom | head -c $((length * 2))
    fi
}

ensure_jwt_env() {
    if grep -Eq '^JWT_SECRET_KEY=.+' .env && grep -Eq '^YUXI_INSTANCE_ID=.+' .env; then
        return
    fi

    echo "JWT security settings are missing in .env."
    read -s -p "Please enter your JWT_SECRET_KEY (press Enter to auto-generate): " JWT_SECRET_KEY
    echo ""
    if [ -z "$JWT_SECRET_KEY" ]; then
        JWT_SECRET_KEY=$(generate_hex 32)
        echo "Generated JWT_SECRET_KEY and saved it to .env."
    fi

    read -p "Please enter your YUXI_INSTANCE_ID (press Enter to auto-generate): " YUXI_INSTANCE_ID
    if [ -z "$YUXI_INSTANCE_ID" ]; then
        YUXI_INSTANCE_ID="instance-$(generate_hex 8)"
        echo "Generated YUXI_INSTANCE_ID and saved it to .env."
    fi

    cat >> .env << EOF

# JWT security settings
JWT_SECRET_KEY=${JWT_SECRET_KEY}
YUXI_INSTANCE_ID=${YUXI_INSTANCE_ID}
EOF
}

# 把"宿主机端口"和"前端浏览器链接"两个核心可调点写入 .env（缺失时追加）。
# 改 .env 中这几个值即可调整：容器端口映射、API 预签 URL、前端控制台跳转链接。
ensure_port_env() {
    local port_vars=(
        "REDIS_HOST_PORT"
        "MINIO_API_HOST_PORT"
        "MINIO_CONSOLE_HOST_PORT"
        "MILVUS_GRPC_HOST_PORT"
        "MILVUS_HEALTH_HOST_PORT"
    )
    local missing=false
    for var in "${port_vars[@]}"; do
        if ! grep -Eq "^${var}=" .env; then
            missing=true
            break
        fi
    done
    if [ "$missing" = false ]; then
        return
    fi

    cat >> .env << 'EOF'

# === 宿主机端口（部署时按本机环境调整，避免与其他服务冲突） ===
# 留空 = 用默认值；改这里一处，所有相关组件（容器映射、浏览器链接、API 预签 URL）自动跟随。
# 与同一台机器上其他项目（smart_ticket 等）冲突时，按需把任一项改为空闲端口。
REDIS_HOST_PORT=6379
MINIO_API_HOST_PORT=9000
MINIO_CONSOLE_HOST_PORT=9001
MILVUS_GRPC_HOST_PORT=19530
MILVUS_HEALTH_HOST_PORT=9091

# === 浏览器访问外部地址（前端打开 MinIO / Milvus 控制台用） ===
# 默认值与上方宿主机端口一致；如改了宿主机端口，这里要同步改。
VITE_MINIO_CONSOLE_URL=http://localhost:9001
VITE_MILVUS_WEBUI_URL=http://localhost:9091/webui/
EOF
    echo "✅ 已写入宿主机端口默认配置（如需调整请编辑 .env）"
}

echo "🚀 Initializing Yuxi project..."
echo "=================================="

# Check if .env file exists
if [ -f ".env" ]; then
    echo "✅ .env file already exists. Skipping environment setup."
    ensure_jwt_env
    ensure_port_env
else
    echo "📝 .env file not found. Let's set up your environment variables."
    echo ""

    # Get SILICONFLOW_API_KEY
    echo "🔑 SiliconFlow API Key required"
    echo "Get your API key from: https://cloud.siliconflow.cn/i/Eo5yTHGJ"
    while true; do
        read -s -p "Please enter your SILICONFLOW_API_KEY: " SILICONFLOW_API_KEY
        echo ""
        if [ -z "$SILICONFLOW_API_KEY" ]; then
            echo "❌ API Key cannot be empty. Please try again."
        else
            break
        fi
    done

    # Get TAVILY_API_KEY (optional)
    echo ""
    echo "🔍 Tavily API Key (optional) - for search service"
    echo "Get your API key from: https://app.tavily.com/"
    read -p "Please enter your TAVILY_API_KEY (press Enter to skip): " TAVILY_API_KEY

    echo ""
    echo "JWT security settings"
    read -s -p "Please enter your JWT_SECRET_KEY (press Enter to auto-generate): " JWT_SECRET_KEY
    echo ""
    if [ -z "$JWT_SECRET_KEY" ]; then
        JWT_SECRET_KEY=$(generate_hex 32)
        echo "Generated JWT_SECRET_KEY and saved it to .env."
    fi

    read -p "Please enter your YUXI_INSTANCE_ID (press Enter to auto-generate): " YUXI_INSTANCE_ID
    if [ -z "$YUXI_INSTANCE_ID" ]; then
        YUXI_INSTANCE_ID="instance-$(generate_hex 8)"
        echo "Generated YUXI_INSTANCE_ID and saved it to .env."
    fi

    # Create .env file
    cat > .env << EOF
# SiliconFlow API Key (required)
SILICONFLOW_API_KEY=${SILICONFLOW_API_KEY}

# Tavily API Key (optional - for search service)
EOF

    if [ -n "$TAVILY_API_KEY" ]; then
        echo "TAVILY_API_KEY=${TAVILY_API_KEY}" >> .env
    fi

    cat >> .env << EOF

# JWT security settings
JWT_SECRET_KEY=${JWT_SECRET_KEY}
YUXI_INSTANCE_ID=${YUXI_INSTANCE_ID}
EOF

    echo "✅ .env file created successfully!"
    ensure_port_env
fi

echo ""
echo "📦 Pulling Docker images..."
echo "========================="

# List of Docker images to pull
images=(
    "python:3.12-slim"
    "node:24-slim"
    "node:24-alpine"
    "milvusdb/milvus:v2.5.6"
    "neo4j:5.26"
    "minio/minio:RELEASE.2023-03-20T20-16-18Z"
    "ghcr.io/astral-sh/uv:0.7.2"
    "nginx:alpine"
    "quay.io/coreos/etcd:v3.5.5"
    "postgres:16"
    "redis:7-alpine"
)

# Pull each image
for image in "${images[@]}"; do
    echo "🔄 Pulling ${image}..."
    if bash scripts/pull_image.sh "$image"; then
        echo "✅ Successfully pulled ${image}"
    else
        echo "❌ Failed to pull ${image}"
        exit 1
    fi
done

echo "🔄 Pulling enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest..."
docker pull enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest
echo "✅ Successfully pulled enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest"

echo ""
echo "🎉 Initialization complete!"
echo "=========================="
echo "You can now run: docker compose up -d --build"
echo "This will start all services in development mode with hot-reload enabled."
