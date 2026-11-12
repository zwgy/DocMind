#!/usr/bin/env bash
set -euo pipefail

# 联网 Linux 从 Git 快照交付，内网只接收镜像和已准备好的应用文件。
main() {
    local mode=full release="${1:-}" base="" output_parent="${2:-$PWD/dist-offline}"
    if [ "$release" = --help ] || [ "$release" = -h ]; then usage; return; fi
    if [ "$release" = --code-only ]; then
        mode=code
        release="${2:-}"
        base="${3:-}"
        output_parent="${4:-$PWD/dist-offline}"
        [ "$#" -ge 3 ] && [ "$#" -le 4 ] && [ -d "$base" ] || { usage >&2; return 2; }
        base="$(cd "$base" && pwd)"
    else
        [ "$#" -ge 1 ] && [ "$#" -le 2 ] || { usage >&2; return 2; }
    fi
    validate_release "$release"
    [ "$(uname -s)/$(uname -m)" = Linux/x86_64 ] || { echo "请在联网 Linux x86_64 构建机执行。" >&2; return 1; }
    docker info >/dev/null
    mkdir -p "$output_parent"
    output_parent="$(cd "$output_parent" && pwd)"
    cd "$(dirname "${BASH_SOURCE[0]}")/.."
    [ "$(git branch --show-current)" = main ] || { echo "正式交付请从 main 分支打包。" >&2; return 1; }
    git diff --quiet HEAD -- || { echo "请先提交已跟踪源码的修改，再生成发布包。" >&2; return 1; }
    local commit runtime="$release" delivery application compose_version=v2.29.7
    commit="$(git rev-parse HEAD)"
    delivery="$output_parent/docmind-offline-$release"
    [ ! -e "$delivery" ] || { echo "发布目录已存在，请使用新标签或其他输出目录。" >&2; return 1; }
    build_directory="$(mktemp -d "$output_parent/.docmind-build-XXXXXX")"
    trap cleanup EXIT
    mkdir -p "$build_directory/source" "$build_directory/application" "$delivery"
    git archive HEAD | tar -xf - -C "$build_directory/source"
    application="$build_directory/application"
    cd "$build_directory/source"

    # 前端依赖变化只影响静态产物；后台依赖、系统工具变化则必须更新环境。
    sha256sum backend/pyproject.toml backend/package/pyproject.toml backend/uv.lock \
        backend/package.json backend/package-lock.json docker/api.Dockerfile \
        docker/web.Dockerfile docker/chat-iframe.Dockerfile docker/sandbox_runtime/Dockerfile \
        docker/sandbox_provisioner/Dockerfile docker/sandbox_provisioner/requirements.txt \
        scripts/offline/prepare_offline_resources.py > "$application/runtime-inputs.sha256"
    if [ "$mode" = code ]; then
        cmp -s "$application/runtime-inputs.sha256" "$base/runtime-inputs.sha256" || {
            echo "运行环境依赖或构建文件已变化，请生成完整分包。" >&2; return 1;
        }
        runtime="$(cat "$base/runtime-release.txt")"
        validate_release "$runtime"
    else
        build_runtime_images "$release" "$commit"
    fi
    docker run --rm --network none --entrypoint python -v "$PWD:/source:ro" -v "$application:/output" \
        "yuxi-api:$runtime" /source/scripts/offline/prepare_compose.py /source /output "$runtime"
    mkdir -p "$application/app/backend" "$application/app/docker/sandbox_provisioner" \
        "$application/app/docker/nginx" "$application/app/chat-iframe" "$application/bin" \
        "$application/scripts" "$application/docker/sandbox_provisioner"
    cp -a backend/package backend/server "$application/app/backend/"
    cp docker/sandbox_provisioner/app.py "$application/app/docker/sandbox_provisioner/"
    cp docker/nginx/nginx.conf docker/nginx/default.conf "$application/app/docker/nginx/"
    cp chat-iframe/nginx.conf "$application/app/chat-iframe/"
    prepare_frontends "$mode" "$release" "$application"
    cp scripts/offline/deploy.sh "$application/deploy.sh"
    cp scripts/manage.sh "$application/scripts/manage.sh"
    cp docker/sandbox_provisioner/sandbox.env "$application/docker/sandbox_provisioner/sandbox.env.template"
    cp docs/advanced/offline-deployment.md "$application/DEPLOY.md"
    printf '%s\n' "$runtime" > "$application/runtime-release.txt"
    printf 'release=%s\ncommit=%s\nruntime=%s\nplatform=linux/amd64\ncompose=%s\n' \
        "$release" "$commit" "$runtime" "$compose_version" > "$application/release.txt"
    if [ "$mode" = code ]; then
        cp "$base/images.sha256" "$application/images.sha256"
    else
        docker run --rm --network none --entrypoint python "yuxi-api:$runtime" /app/verify_offline_resources.py
        download_compose "$application/bin" "$compose_version"
        export_image_groups "$application" "$delivery" "$runtime"
    fi
    (cd "$application" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
    tar -I 'gzip -6' -cf "$delivery/docmind-app-$release.tar.gz" -C "$application" .
    cp "$application/DEPLOY.md" "$delivery/DEPLOY.md"
    cp "$application/release.txt" "$delivery/release.txt"
    (cd "$delivery" && sha256sum ./*.tar.gz > SHA256SUMS)
    echo "交付目录：$delivery"
    echo "应用包 docmind-app-$release.tar.gz；运行环境版本 $runtime；源码提交 $commit。"
    echo "每包内容及部署、更新命令见 $delivery/DEPLOY.md。"
}

usage() {
    echo "首次分包：bash scripts/package-offline.sh <release> [output-parent]"
    echo "仅更新代码：bash scripts/package-offline.sh --code-only <release> <已解开的应用包目录> [output-parent]"
}
validate_release() {
    [[ "$1" =~ ^[a-z0-9][a-z0-9_.-]{0,100}$ ]] || { echo "发布标签仅用小写字母、数字、点、下划线、连字符。" >&2; return 2; }
}
build_runtime_images() {
    local release="$1" commit="$2" image
    for image in api web chat-iframe sandbox-provisioner sandbox-runtime; do
        if docker image inspect "yuxi-$image:$release" >/dev/null 2>&1; then
            echo "本机已有 yuxi-$image:$release，请使用新的发布标签。" >&2; return 1
        fi
    done
    docker build --platform linux/amd64 --label "org.opencontainers.image.revision=$commit" -f docker/api.Dockerfile -t "yuxi-api:$release" .
    docker build --platform linux/amd64 --label "org.opencontainers.image.revision=$commit" -f docker/web.Dockerfile --target production -t "yuxi-web:$release" .
    docker build --platform linux/amd64 --label "org.opencontainers.image.revision=$commit" -f docker/chat-iframe.Dockerfile --target production -t "yuxi-chat-iframe:$release" .
    docker build --platform linux/amd64 --label "org.opencontainers.image.revision=$commit" -t "yuxi-sandbox-provisioner:$release" docker/sandbox_provisioner
    docker build --platform linux/amd64 --label "org.opencontainers.image.revision=$commit" -t "yuxi-sandbox-runtime:$release" docker/sandbox_runtime
}
prepare_frontends() {
    local mode="$1" release="$2" application="$3" name image path
    for name in web chat-iframe; do
        image="yuxi-$name:$release"
        path=/usr/share/nginx/html
        if [ "$mode" = code ]; then
            image="yuxi-offline-$name-build:$release"
            frontend_build_images+=("$image")
            docker build --platform linux/amd64 -f "docker/$name.Dockerfile" --target build-stage -t "$image" .
            path=/app/dist
        elif [ "$name" = chat-iframe ]; then
            path=/usr/share/nginx/html/chat-iframe
        fi
        mkdir -p "$application/app/$name/dist"
        export_container="$(docker create "$image")"
        docker cp "$export_container:$path/." "$application/app/$name/dist/"
        docker rm "$export_container" >/dev/null
        export_container=""
    done
    # Nginx 非 root worker 仍需可读静态文件；挂载保持只读。
    find "$application/app/web/dist" "$application/app/chat-iframe/dist" -type d -exec chmod 755 {} +
    find "$application/app/web/dist" "$application/app/chat-iframe/dist" -type f -exec chmod 644 {} +
}
export_image_groups() {
    local application="$1" delivery="$2" runtime="$3" image platform group archive
    local images=()
    mapfile -t images < "$application/images.txt"
    for image in "${images[@]}"; do
        case "$image" in
            yuxi-*) ;;
            *) docker image inspect "$image" >/dev/null 2>&1 || docker pull --platform linux/amd64 "$image" ;;
        esac
        platform="$(docker image inspect "$image" --format '{{.Os}}/{{.Architecture}}')"
        [ "$platform" = linux/amd64 ] || { echo "镜像平台不符：$image ($platform)" >&2; return 1; }
    done
    docker image inspect "${images[@]}" > "$application/image-manifest.json"
    for group in infra app sandbox; do
        mapfile -t images < "$application/images-$group.txt"
        archive="docmind-images-$group-$runtime.tar.gz"
        docker save "${images[@]}" | gzip -6 > "$delivery/$archive"
        (cd "$delivery" && sha256sum "$archive") >> "$application/images.sha256"
    done
}
download_compose() {
    local directory="$1" version="$2" asset=docker-compose-linux-x86_64
    local url="https://github.com/docker/compose/releases/download/$version"
    curl --fail --location --retry 2 "$url/$asset" -o "$directory/$asset"
    curl --fail --location --retry 2 "$url/$asset.sha256" -o "$directory/$asset.sha256"
    (cd "$directory" && sha256sum --check "$asset.sha256")
    mv "$directory/$asset" "$directory/docker-compose"
    rm "$directory/$asset.sha256"
    chmod 0755 "$directory/docker-compose"
    "$directory/docker-compose" version
}
cleanup() {
    [ -z "${export_container:-}" ] || docker rm -f "$export_container" >/dev/null
    if [ "${#frontend_build_images[@]}" -gt 0 ]; then
        docker image rm "${frontend_build_images[@]}" >/dev/null
    fi
    rm -rf -- "$build_directory"
}
frontend_build_images=()
main "$@"
