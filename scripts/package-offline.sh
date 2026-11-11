#!/usr/bin/env bash
set -euo pipefail

# 只在联网 Linux 构建机执行；从 Git 快照构建，绝不将开发配置或业务数据带入交付包。
main() {
    local release="${1:-}" output_parent="${2:-$PWD/dist-offline}"
    if [ "$release" = --help ] || [ "$release" = -h ]; then
        echo "用法：bash scripts/package-offline.sh <release> [output-parent]"
        echo "需要：联网 Linux x86_64、Git、Docker 构建能力、curl、tar、gzip、sha256sum。"
        return
    fi
    validate_build_input "$release" "$#"
    cd "$(dirname "${BASH_SOURCE[0]}")/.."
    git diff --quiet HEAD -- || { echo "请先提交已跟踪源码的修改，再生成发布包。" >&2; return 1; }
    local commit package archive compose_version=v2.29.7
    commit="$(git rev-parse HEAD)"
    mkdir -p "$output_parent"
    output_parent="$(cd "$output_parent" && pwd)"
    package="$output_parent/docmind-offline-$release"
    archive="$package.tar.gz"
    [ ! -e "$package" ] && [ ! -e "$archive" ] || { echo "发布目录已存在，请使用新标签或其他输出目录。" >&2; return 1; }
    # 同一标签不能复用于不同源码；避免离线升级后出现部分服务仍使用旧镜像。
    local image
    for image in api web chat-iframe sandbox-provisioner sandbox-runtime; do
        if docker image inspect "yuxi-$image:$release" >/dev/null 2>&1; then
            echo "本机已有 yuxi-$image:$release，请使用新的发布标签。" >&2
            return 1
        fi
    done
    mkdir -p "$package/bin" "$package/scripts" "$package/docker/sandbox_provisioner"
    build_directory="$(mktemp -d "$output_parent/.docmind-build-XXXXXX")"
    trap 'rm -rf -- "$build_directory"' EXIT
    git archive HEAD | tar -xf - -C "$build_directory"
    cd "$build_directory"

    docker build --platform linux/amd64 --label "org.opencontainers.image.revision=$commit" -f docker/api.Dockerfile -t "yuxi-api:$release" .
    docker build --platform linux/amd64 --label "org.opencontainers.image.revision=$commit" -f docker/web.Dockerfile --target production -t "yuxi-web:$release" .
    docker build --platform linux/amd64 --label "org.opencontainers.image.revision=$commit" -f docker/chat-iframe.Dockerfile --target production -t "yuxi-chat-iframe:$release" .
    docker build --platform linux/amd64 --label "org.opencontainers.image.revision=$commit" -t "yuxi-sandbox-provisioner:$release" docker/sandbox_provisioner
    docker build --platform linux/amd64 --label "org.opencontainers.image.revision=$commit" -t "yuxi-sandbox-runtime:$release" docker/sandbox_runtime

    docker run --rm --network none --entrypoint python -v "$build_directory:/source:ro" -v "$package:/output" \
        "yuxi-api:$release" /source/scripts/offline/prepare_compose.py /source /output "$release"
    docker run --rm --network none --entrypoint python "yuxi-api:$release" /app/verify_offline_resources.py
    cp scripts/offline/deploy.sh "$package/deploy.sh"
    cp scripts/manage.sh "$package/scripts/manage.sh"
    cp docker/sandbox_provisioner/sandbox.env "$package/docker/sandbox_provisioner/sandbox.env.template"
    cp docs/advanced/offline-deployment.md "$package/DEPLOY.md"
    download_compose "$package/bin" "$compose_version"

    local images=() platform
    mapfile -t images < "$package/images.txt"
    for image in "${images[@]}"; do
        case "$image" in yuxi-*) ;; *) docker pull --platform linux/amd64 "$image" ;; esac
        platform="$(docker image inspect "$image" --format '{{.Os}}/{{.Architecture}}')"
        [ "$platform" = linux/amd64 ] || { echo "镜像平台不符：$image ($platform)" >&2; return 1; }
    done
    docker image inspect "${images[@]}" > "$package/image-manifest.json"
    printf 'release=%s\ncommit=%s\nplatform=linux/amd64\ncompose=%s\n' "$release" "$commit" "$compose_version" > "$package/release.txt"
    docker save -o "$package/images.tar" "${images[@]}"
    cd "$package"
    find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
    cd "$output_parent"
    tar -I 'gzip -1' -cf "$archive" "$(basename "$package")"
    sha256sum "$(basename "$archive")" > "$archive.sha256"
    echo "交付包：$archive"
    echo "源码提交：$commit；资源断网检查已通过，完整业务验收及目标机冷启动仍按 DEPLOY.md 执行。"
}

validate_build_input() {
    [ "$2" -ge 1 ] && [ "$2" -le 2 ] && [[ "$1" =~ ^[a-z0-9][a-z0-9_.-]{0,100}$ ]] || {
        echo "用法：bash scripts/package-offline.sh <release> [output-parent]；标签仅用小写字母、数字、点、下划线、连字符。" >&2
        return 2
    }
    [ "$(uname -s)/$(uname -m)" = Linux/x86_64 ] || { echo "请在联网 Linux x86_64 构建机执行。" >&2; return 1; }
    docker info >/dev/null
}

download_compose() {
    local directory="$1" version="$2"
    local asset=docker-compose-linux-x86_64
    local url="https://github.com/docker/compose/releases/download/$version"
    curl --fail --location --retry 2 "$url/$asset" -o "$directory/$asset"
    curl --fail --location --retry 2 "$url/$asset.sha256" -o "$directory/$asset.sha256"
    (cd "$directory" && sha256sum --check "$asset.sha256")
    mv "$directory/$asset" "$directory/docker-compose"
    rm "$directory/$asset.sha256"
    chmod 0755 "$directory/docker-compose"
    "$directory/docker-compose" version
}

main "$@"
