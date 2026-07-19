#!/usr/bin/env bash
set -euo pipefail

# DocMind Docker Compose 统一管理入口。
#
# 用法：bash scripts/manage.sh <dev|prod> <action> [service]
# - dev  使用 .env 和 docker-compose.yml，保留前端热更新。
# - prod 使用 .env.prod 和 docker-compose.prod.yml，首次部署会交互式生成
#        启动所需的安全配置；模型、跨域等非启动必需项仍由运维按需补充。
# - [service] 是 Compose 服务名，例如 api、web、chat-iframe，不是容器名。
# - 需要 GPU OCR 等 profile=all 服务时，在命令前设置 COMPOSE_PROFILES=all。

usage() {
    cat <<'EOF'
用法: bash scripts/manage.sh <dev|prod> <action> [service]

操作:
  init     初始化环境文件；prod 会交互生成 .env.prod 的启动必需安全配置
  deploy   校验配置、构建镜像并后台启动服务
  start    不重新构建镜像，启动已有服务
  stop     停止服务，但保留容器
  restart  重启服务
  down     删除容器和 Compose 网络，保留命名卷
  status   查看服务状态
  logs     跟随最近 200 行日志
  build    只构建镜像，不启动服务
  config   只校验环境变量和 Compose 最终配置
  help     显示本帮助

示例:
  # 初始化生产配置；首次 prod deploy 缺少 .env.prod 时也会自动执行此步骤
  bash scripts/manage.sh prod init

  # 开发、生产一键部署
  bash scripts/manage.sh dev deploy
  bash scripts/manage.sh prod deploy

  # 管理单个 Compose 服务
  bash scripts/manage.sh prod logs api

  # 启动包含 GPU OCR 的全部服务
  COMPOSE_PROFILES=all bash scripts/manage.sh prod deploy
EOF
}

environment="${1:-}"
action="${2:-help}"
service="${3:-}"

if [ "$environment" = "help" ] || [ "$action" = "help" ]; then
    usage
    exit 0
fi
if [ $# -gt 3 ]; then
    usage >&2
    exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "${script_dir}/.." && pwd)"
cd "$project_root"

case "$environment" in
    dev)
        env_file=".env"
        compose_file="docker-compose.yml"
        ;;
    prod)
        env_file=".env.prod"
        compose_file="docker-compose.prod.yml"
        ;;
    *)
        echo "Error: environment must be dev or prod." >&2
        usage >&2
        exit 2
        ;;
esac

# 生成仅由十六进制组成的随机值，避免密码中的 shell/Compose 特殊字符引入转义问题。
generate_hex() {
    local length="$1"
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex "$length"
    else
        tr -dc 'a-f0-9' < /dev/urandom | head -c $((length * 2))
    fi
}

# 写入 .env.prod 的一个值：模板中即使是被注释的 KEY= 行也会原地启用，
# 不存在时才追加，避免同一变量保留多份配置而难以确认最终生效值。
write_prod_env_value() {
    local key="$1"
    local value="$2"
    # API 密钥可能包含 &、| 或反斜杠；先转义再交给 sed，避免把密钥当作替换语法。
    local escaped_value="${value//\\/\\\\}"
    escaped_value="${escaped_value//&/\\&}"
    escaped_value="${escaped_value//|/\\|}"
    if grep -Eq "^[[:space:]]*#?[[:space:]]*${key}=" "$env_file"; then
        sed -i.bak -E "s|^[[:space:]]*#?[[:space:]]*${key}=.*|${key}=${escaped_value}|" "$env_file"
        rm -f "${env_file}.bak"
    else
        printf "\n%s=%s\n" "$key" "$value" >> "$env_file"
    fi
}

# 生产配置只生成启动和安全必需项，绝不输出密码；模型密钥、跨域、GPU 等
# 依赖部署拓扑的项由运维在生成后按实际环境填写，避免脚本猜测业务配置。
initialize_production_env() {
    if [ -f "$env_file" ]; then
        echo "$env_file 已存在，保留现有配置。"
        return 0
    fi
    if [ ! -f .env.template ]; then
        echo "Error: .env.template not found; cannot create $env_file." >&2
        return 1
    fi
    if [ ! -t 0 ]; then
        echo "Error: production initialization requires an interactive terminal to create $env_file." >&2
        return 1
    fi

    local answer=""
    read -r -p "将创建 $env_file 并生成生产密码，是否继续？[y/N]: " answer
    case "$answer" in
        y|Y|yes|YES) ;;
        *)
            echo "已取消生产配置初始化。"
            return 1
            ;;
    esac

    cp .env.template "$env_file"
    sed -i.bak $'s/\r$//' "$env_file"
    rm -f "${env_file}.bak"
    write_prod_env_value YUXI_ENV production
    write_prod_env_value JWT_SECRET_KEY "$(generate_hex 32)"
    write_prod_env_value YUXI_INSTANCE_ID "instance-$(generate_hex 8)"
    write_prod_env_value POSTGRES_PASSWORD "$(generate_hex 32)"
    write_prod_env_value NEO4J_PASSWORD "$(generate_hex 32)"
    write_prod_env_value MINIO_ACCESS_KEY "minio-$(generate_hex 12)"
    write_prod_env_value MINIO_SECRET_KEY "$(generate_hex 32)"

    read -r -p "现在填写 SILICONFLOW_API_KEY 吗？直接回车跳过: " answer
    [ -n "$answer" ] && write_prod_env_value SILICONFLOW_API_KEY "$answer"

    cat <<EOF
✅ 已生成 $env_file 的启动必需安全配置（密钥不会打印到终端）。
部署前请按实际环境检查或补充：
  - SILICONFLOW_API_KEY 或其他模型提供商密钥（不填可启动，但无法正常调用模型）
  - YUXI_CORS_ORIGINS（存在跨域浏览器访问时）
  - CHAT_IFRAME_*（启用外部用户自助换票时）
  - 端口、GPU OCR、Sandbox、MinerU 等部署相关配置
EOF
}

# init 不依赖 Docker；开发环境复用既有初始化脚本，生产环境只创建 .env.prod。
if [ "$action" = "init" ]; then
    if [ "$environment" = "dev" ]; then
        bash scripts/init.sh
    else
        initialize_production_env
    fi
    exit $?
fi

if [ ! -f "$env_file" ]; then
    if [ "$environment" = "dev" ] && [ "$action" = "deploy" ]; then
        echo "缺少 .env，正在运行现有开发环境初始化脚本..."
        bash scripts/init.sh
    elif [ "$environment" = "prod" ] && [ "$action" = "deploy" ]; then
        echo "缺少 .env.prod，正在初始化生产环境配置..."
        initialize_production_env
    else
        echo "Error: $env_file not found." >&2
        [ "$environment" = "prod" ] && echo "请先执行：bash scripts/manage.sh prod init" >&2
        exit 1
    fi
fi

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    echo "Error: Docker Engine and Docker Compose v2 are required." >&2
    exit 1
fi

# Compose 参数固定在此处，确保每个操作都使用与环境对应的文件，避免误把开发变量带入生产。
compose=(docker compose --env-file "$env_file" -f "$compose_file")

# 先打印完整命令，再执行，便于运维从日志中复制排查；不打印 .env 的具体值。
run_compose() {
    printf '+ '
    printf '%q ' "${compose[@]}" "$@"
    printf '\n'
    "${compose[@]}" "$@"
}

run_compose_with_target() {
    # macOS 系统 Bash 仍常见 3.2 版本；在 set -u 下展开空数组会报 unbound variable。
    # 这里按是否指定 service 决定是否追加参数，避免空数组兼容性问题。
    if [ -n "$service" ]; then
        run_compose "$@" "$service"
    else
        run_compose "$@"
    fi
}

# 读取最后一个同名变量，和 Compose 对重复变量的覆盖顺序保持一致；仅用于安全校验。
read_env_value() {
    local key="$1"
    local value
    value="$(grep -E "^${key}=" "$env_file" 2>/dev/null | tail -n1 | cut -d= -f2- || true)"
    printf '%s' "$value" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]\r]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

# 生产发布前门禁：拒绝模板空值、占位符和公开默认密码。
# chat-iframe 自助换票开启时，来源与 Origin 白名单也必须显式配置。
validate_prod_env() {
    local failed=0
    local key value
    local required=(JWT_SECRET_KEY YUXI_INSTANCE_ID POSTGRES_PASSWORD NEO4J_PASSWORD MINIO_ACCESS_KEY MINIO_SECRET_KEY)

    for key in "${required[@]}"; do
        value="$(read_env_value "$key")"
        if [ -z "$value" ] || [[ "$value" == \#* ]] || [[ "$value" == __REPLACE_ME__* ]]; then
            echo "Error: $key must be configured in .env.prod." >&2
            failed=1
        fi
    done

    [ "$(read_env_value JWT_SECRET_KEY)" = "yuxi_know_secure_key" ] && echo "Error: JWT_SECRET_KEY uses the public default." >&2 && failed=1
    [ "$(read_env_value POSTGRES_PASSWORD)" = "postgres" ] && echo "Error: POSTGRES_PASSWORD uses the public default." >&2 && failed=1
    [ "$(read_env_value NEO4J_PASSWORD)" = "0123456789" ] && echo "Error: NEO4J_PASSWORD uses the public default." >&2 && failed=1
    [ "$(read_env_value MINIO_ACCESS_KEY)" = "minioadmin" ] && echo "Error: MINIO_ACCESS_KEY uses the public default." >&2 && failed=1
    [ "$(read_env_value MINIO_SECRET_KEY)" = "minioadmin" ] && echo "Error: MINIO_SECRET_KEY uses the public default." >&2 && failed=1

    case "$(read_env_value CHAT_IFRAME_AUTO_LOGIN_ENABLED | tr '[:upper:]' '[:lower:]')" in
        1|true|yes|on)
            for key in CHAT_IFRAME_ALLOWED_SOURCES CHAT_IFRAME_ALLOWED_ORIGINS; do
                if [ -z "$(read_env_value "$key")" ]; then
                    echo "Error: $key is required when chat-iframe auto login is enabled." >&2
                    failed=1
                fi
            done
            ;;
    esac

    [ "$failed" -eq 0 ]
}

case "$action" in
    deploy)
        # deploy 固定先做配置解析，避免镜像构建或容器变更后才发现变量错误。
        [ "$environment" = "prod" ] && validate_prod_env
        run_compose config --quiet
        run_compose_with_target up -d --build
        run_compose ps
        ;;
    start)
        run_compose_with_target up -d --no-build
        ;;
    stop)
        run_compose_with_target stop
        ;;
    restart)
        run_compose_with_target restart
        ;;
    down)
        # down 是整个 Compose 项目的销毁操作，单服务场景应使用 stop，防止误解行为。
        if [ -n "$service" ]; then
            echo "Error: down does not accept a service; use stop for one service." >&2
            exit 2
        fi
        run_compose down
        ;;
    status)
        run_compose_with_target ps
        ;;
    logs)
        run_compose_with_target logs --tail 200 -f
        ;;
    build)
        run_compose_with_target build
        ;;
    config)
        [ "$environment" = "prod" ] && validate_prod_env
        run_compose config --quiet
        echo "Compose configuration is valid."
        ;;
    *)
        echo "Error: unsupported action: $action" >&2
        usage >&2
        exit 2
        ;;
esac
