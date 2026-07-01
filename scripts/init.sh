#!/bin/bash

# Yuxi Initialization Script for Bash/Linux/macOS
# 基于 .env.template 蓝本生成 .env；询问缺失的必填项，自动生成缺失的密钥。

set -e

###############################################################################
# 通用 helper
###############################################################################

# 生成指定字节数的随机十六进制串（openssl 优先，回退 urandom）
generate_hex() {
    local length="$1"
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex "$length"
    else
        tr -dc 'a-f0-9' < /dev/urandom | head -c $((length * 2))
    fi
}

# 读取 .env 中 VAR 的当前值（可能为空）；VAR 不存在则返回空串
read_env_value() {
    local var_name="$1"
    grep -E "^${var_name}=" .env 2>/dev/null | head -n1 | cut -d= -f2-
}

# 幂等写入 .env：
#   - 已存在且有真值（等号后第一个字符非空白/非 #） → 跳过（保护用户已有配置）
#   - 存在但为模板占位（KEY=  # 注释）                 → sed 原地替换
#   - 完全不存在                                         → 追加到末尾
ensure_env_var() {
    local var_name="$1"
    local var_value="$2"
    # 真值判定：等号后第一个字符是真实值字符（非空白、非注释符号 #）
    if grep -Eq "^${var_name}=[^[:space:]#]" .env; then
        return 0
    fi
    if grep -Eq "^${var_name}=" .env; then
        # 前面 grep 已保证模式能匹配；sed -i.bak 兼容 GNU/BSD sed
        sed -i.bak "s|^${var_name}=.*|${var_name}=${var_value}|" .env
        rm -f .env.bak
    else
        printf "\n%s=%s\n" "$var_name" "$var_value" >> .env
    fi
}

# 删除 .env 中所有 KEY=  后跟空白/可选注释的行（即模板占位但用户未填的行）
# 保留所有 KEY=真值 行（包括基础设施默认值和用户实际填的值）
cleanup_empty_placeholders() {
    sed -i.bak -E '/^[A-Z_][A-Z0-9_]*=[[:space:]]*(#.*)?$/d' .env
    rm -f .env.bak
}

# 询问用户输入并写回 .env：
#   - 已有非空值 → 跳过
#   - 必填（required）：非交互式写入明显占位符 + 警告（不退出，保证后续密钥能继续生成）；交互式空输入循环要求重新输入
#   - 可选（optional）：非交互式静默跳过；交互式空输入也跳过
ask_or_skip() {
    local var_name="$1"
    local prompt="$2"
    local required="${3:-required}"  # required | optional

    if grep -Eq "^${var_name}=[^[:space:]#]" .env; then
        return 0
    fi

    # 非交互式环境（管道 / 重定向）处理：required 写占位符 + 警告，optional 静默跳过
    if [ ! -t 0 ]; then
        if [ "$required" = "required" ]; then
            echo "⚠️  非交互式环境无法询问 ${var_name}，已写入占位符 __REPLACE_ME__${var_name}__；请事后手动编辑 .env 替换为真实值" >&2
            ensure_env_var "$var_name" "__REPLACE_ME__${var_name}__"
        fi
        return 0
    fi

    local input
    if [ "$required" = "required" ]; then
        while [ -z "$input" ]; do
            read -r -p "$prompt: " input
            [ -z "$input" ] && echo "❌ 不能为空，请重新输入"
        done
    else
        read -r -p "$prompt (直接回车跳过): " input
    fi

    [ -n "$input" ] && ensure_env_var "$var_name" "$input"
}

# 询问用户输入，传入 fallback 自动生成值：
#   - 已有非空值 → 跳过
#   - 非交互式 → 直接用 fallback（自动生成，不询问）
#   - 交互式：用户输入 → 用用户值；空输入 → 用 fallback
ask_auto() {
    local var_name="$1"
    local prompt="$2"
    local fallback="$3"

    if grep -Eq "^${var_name}=[^[:space:]#]" .env; then
        return 0
    fi

    # 非交互式环境 → 直接用 fallback
    if [ ! -t 0 ]; then
        ensure_env_var "$var_name" "$fallback"
        echo "✅ 已自动生成 $var_name"
        return 0
    fi

    local input
    read -r -p "$prompt（直接回车自动生成）: " input
    if [ -z "$input" ]; then
        input="$fallback"
        echo "✅ 已自动生成 $var_name"
    fi
    ensure_env_var "$var_name" "$input"
}

###############################################################################
# 主流程
###############################################################################

echo "🚀 Initializing Yuxi project..."
echo "=================================="

# 第 1 步：确保 .env 存在（以 .env.template 为蓝本）
if [ ! -f .env ]; then
    if [ ! -f .env.template ]; then
        echo "❌ .env.template 不存在，无法初始化" >&2
        exit 1
    fi
    cp .env.template .env
    echo "✅ 基于 .env.template 创建 .env"
else
    echo "✅ .env 已存在，跳过复制"
fi

# 删除模板占位但用户未填的行（如 TAVILY_API_KEY= ），避免污染用户配置
cleanup_empty_placeholders

# 第 2 步：补齐缺失项
echo ""
echo "🔑 SiliconFlow API Key（首次必填，用于调用大模型）"
echo "Get your API key from: https://cloud.siliconflow.cn/i/Eo5yTHGJ" >&2
ask_or_skip SILICONFLOW_API_KEY "请输入 SILICONFLOW_API_KEY" required

echo ""
echo "🔍 Tavily API Key（可选，用于搜索服务）"
echo "Get your API key from: https://app.tavily.com/" >&2
ask_or_skip TAVILY_API_KEY "请输入 TAVILY_API_KEY" optional

# 第 3 步：补齐缺失的密钥（询问用户，可回车自动生成）
ask_auto JWT_SECRET_KEY "请输入 JWT_SECRET_KEY" "$(generate_hex 32)"
ask_auto YUXI_INSTANCE_ID "请输入 YUXI_INSTANCE_ID" "instance-$(generate_hex 8)"

# 第 4 步：拉取 Docker 镜像
echo ""
echo "📦 Pulling Docker images..."
echo "========================="

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