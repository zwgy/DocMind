#!/usr/bin/env bash
set -euo pipefail

# 交付包内唯一部署入口：所有相对数据目录均相对于本文件，不能随调用目录漂移。
main() {
    local action="${1:-help}"
    cd "$(dirname "${BASH_SOURCE[0]}")"
    case "$action" in
        help|--help|-h) usage; return ;;
        init)
            umask 077
            bash scripts/manage.sh prod init
            initialize_sandbox_configuration
            return
            ;;
        install)
            verify_package
            sha256sum --check --quiet images.sha256
            require_configuration
            initialize_sandbox_configuration
            ensure_compose
            docker info >/dev/null
            compose config --quiet
            local digest archive
            while read -r digest archive; do
                docker load -i "$archive"
            done < images.sha256
            verify_images
            compose up -d --no-build --pull never --wait --wait-timeout 600
            compose ps
            echo "服务启动检查已通过；请按 DEPLOY.md 完成模型、OCR、知识库与沙盒验收。"
            ;;
        update)
            update_application "${2:-}"
            ;;
        status|logs|restart|stop|apply)
            require_configuration
            use_bundle_docker_config
            docker compose version >/dev/null
            case "$action" in
                status) compose ps ;;
                logs) compose logs --tail 200 "${@:2}" ;;
                restart) compose restart "${@:2}" ;;
                stop) compose stop ;;
                apply) compose up -d --no-build --pull never --wait --wait-timeout 600 ;;
            esac
            ;;
        *) usage >&2; return 2 ;;
    esac
}

usage() {
    cat <<'EOF'
用法：bash deploy.sh <init|install|update|status|logs|restart|stop|apply> [参数]
  init     沿用生产初始化流程创建 .env.prod，已有文件保持原样
  install  校验包、补装包内 Compose 插件、导入三个镜像包并离线启动
  update   更新应用：bash deploy.sh update /path/docmind-app-新版本.tar.gz；不导入镜像
  status   查看服务状态
  logs     查看最近 200 行日志，可附服务名（例如 api）
  restart  重启服务，可附服务名
  stop     停止本实例所有服务，保留容器和数据
  apply    修改 .env.prod 后重新创建相关容器，不导入镜像
首次部署：init → 填写 .env.prod → install → 按 DEPLOY.md 验收
EOF
}

update_application() {
    # 先检查再停服务，避免坏包或依赖变化导致不必要的停机；普通更新不碰数据库和本机配置。
    local archive="$1" stage
    [ -f "$archive" ] || { echo "用法：bash deploy.sh update /path/docmind-app-新版本.tar.gz" >&2; return 2; }
    require_configuration
    stage="$(mktemp -d "$PWD/.docmind-update-XXXXXX")"
    update_stage="$stage"
    trap 'rm -rf -- "$update_stage"' EXIT
    tar -xzf "$archive" -C "$stage"
    (cd "$stage" && verify_package)
    if ! cmp -s runtime-release.txt "$stage/runtime-release.txt" || ! cmp -s runtime-inputs.sha256 "$stage/runtime-inputs.sha256"; then
        echo "运行环境不一致，请使用完整分包更新环境，不能仅更新代码。" >&2
        return 1
    fi
    ensure_compose
    docker compose --project-directory "$PWD" --env-file .env.prod -f "$stage/compose.yaml" config --quiet
    verify_images
    local services=(api worker incoming-worker incoming-dispatcher scheduler dispatcher sandbox-provisioner web chat-iframe)
    compose stop "${services[@]}"
    # 整体替换 app，保证新版本已删除的源码不会残留；数据和配置始终在 app 外。
    rm -rf -- app
    mv "$stage/app" app
    cp -a "$stage/." .
    compose up -d --no-build --pull never --no-deps --force-recreate --wait --wait-timeout 600 "${services[@]}"
    echo "应用更新完成，未导入镜像，配置和业务数据已保留。"
}

verify_package() {
    [ -f SHA256SUMS ] || { echo "缺少 SHA256SUMS，拒绝安装。" >&2; return 1; }
    sha256sum --check --quiet SHA256SUMS
}

require_configuration() {
    [ -f .env.prod ] || { echo "请先执行 bash deploy.sh init 并填写 .env.prod。" >&2; return 1; }
}

initialize_sandbox_configuration() {
    # 全局沙盒配置由运维维护，不放进发布校验清单，也不在升级时覆盖。
    local path=docker/sandbox_provisioner/sandbox.env
    if [ ! -f "$path" ]; then
        (umask 077; cp "$path.template" "$path")
    fi
}

use_bundle_docker_config() {
    # 插件和 Docker 客户端配置放在数据盘，不向已满的根分区写入；不影响系统现有配置。
    export DOCKER_CONFIG="$PWD/.docker"
}

ensure_compose() {
    use_bundle_docker_config
    [ "$(uname -s)/$(uname -m)" = Linux/x86_64 ] || { echo "此交付包仅支持 Linux x86_64。" >&2; return 1; }
    if [ ! -x "$DOCKER_CONFIG/cli-plugins/docker-compose" ]; then
        mkdir -p "$DOCKER_CONFIG/cli-plugins"
        install -m 0755 bin/docker-compose "$DOCKER_CONFIG/cli-plugins/docker-compose"
        echo "已安装包内 Compose 插件到 $DOCKER_CONFIG/cli-plugins。"
    fi
    # 安装完成后必须由 Docker 发现插件，不能把文件复制成功当作插件可用。
    docker compose version
}

compose() {
    docker compose --env-file .env.prod -f compose.yaml "$@"
}

verify_images() {
    local image platform
    while IFS= read -r image; do
        platform="$(docker image inspect "$image" --format '{{.Os}}/{{.Architecture}}')"
        [ "$platform" = linux/amd64 ] || { echo "镜像不存在或平台不符：$image ($platform)" >&2; return 1; }
    done < images.txt
}

main "$@"
