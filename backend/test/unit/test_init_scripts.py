from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_init_scripts_keep_auto_generated_env_placeholders():
    """自动生成变量必须留在模板原位置，避免清理空占位符时被提前删掉。"""
    bash = (ROOT / "scripts" / "init.sh").read_text(encoding="utf-8")
    ps1_path = ROOT / "scripts" / "init.ps1"
    ps1 = ps1_path.read_text(encoding="utf-8")

    assert "\nnormalize_env_file\n\n# 删除模板占位" in bash
    assert "JWT_SECRET_KEY|YUXI_INSTANCE_ID" in bash
    assert 'ensure_env_var JWT_SECRET_KEY "$(generate_hex 32)"' in bash
    assert 'ensure_env_var YUXI_INSTANCE_ID "instance-$(generate_hex 8)"' in bash
    assert bash.index('ensure_env_var JWT_SECRET_KEY "$(generate_hex 32)"') < bash.index(
        "ask_or_skip SILICONFLOW_API_KEY"
    )
    assert '@("JWT_SECRET_KEY", "YUXI_INSTANCE_ID")' in ps1
    assert 'Update-EnvVar "JWT_SECRET_KEY" (New-RandomHex 32)' in ps1
    assert 'Update-EnvVar "YUXI_INSTANCE_ID" ("instance-" + (New-RandomHex 8))' in ps1
    assert ps1.index('Update-EnvVar "JWT_SECRET_KEY" (New-RandomHex 32)') < ps1.index(
        'Read-UserInput "SILICONFLOW_API_KEY"'
    )
    assert ps1_path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_management_scripts_share_the_same_safe_compose_contract():
    """双平台入口必须使用同一环境文件和生产发布前门禁。"""
    bash = (ROOT / "scripts" / "manage.sh").read_text(encoding="utf-8")
    powershell = (ROOT / "scripts" / "manage.ps1").read_text(encoding="utf-8-sig")

    actions = (
        "deploy",
        "start",
        "stop",
        "restart",
        "down",
        "status",
        "logs",
        "build",
        "config",
    )
    for action in actions:
        assert f"{action})" in bash
        assert f'"{action}"' in powershell
    assert '[ "$action" = "init" ]' in bash
    assert '$Action -eq "init"' in powershell

    assert 'compose=(docker compose --env-file "$env_file" -f "$compose_file")' in bash
    assert '$composeArgs = @("compose", "--env-file", $envFile, "-f", $composeFile)' in powershell
    assert bash.index("run_compose config --quiet") < bash.index("run_compose_with_target up -d --build")
    assert powershell.index('Invoke-Compose @("config", "--quiet")') < powershell.index(
        'Invoke-Compose (@("up", "-d", "--build")'
    )
    for key in (
        "JWT_SECRET_KEY",
        "POSTGRES_PASSWORD",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
    ):
        assert key in bash
        assert key in powershell
    assert (ROOT / "scripts" / "manage.ps1").read_bytes().startswith(b"\xef\xbb\xbf")


def test_production_compose_requires_security_credentials_without_public_defaults():
    """直接调用生产 Compose 时也必须执行密钥门禁，不能依赖管理脚本才安全。"""

    template = (ROOT / ".env.template").read_text(encoding="utf-8")
    production = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")

    for key in (
        "JWT_SECRET_KEY",
        "YUXI_INSTANCE_ID",
        "POSTGRES_PASSWORD",
        "NEO4J_PASSWORD",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
    ):
        assert f"{key}=" in template
        assert f"${{{key}:?" in production

    for public_default in ("POSTGRES_PASSWORD:-postgres", "NEO4J_PASSWORD:-0123456789", "MINIO_ACCESS_KEY:-minioadmin"):
        assert public_default not in production


def test_node_images_share_version_24_and_keep_required_os_variants():
    """API 需要 Debian/glibc 的 slim，前端构建可复用更小的 Alpine。"""
    api = (ROOT / "docker" / "api.Dockerfile").read_text(encoding="utf-8")
    web = (ROOT / "docker" / "web.Dockerfile").read_text(encoding="utf-8")
    iframe = (ROOT / "docker" / "chat-iframe.Dockerfile").read_text(encoding="utf-8")

    assert "node:24-slim" in api
    assert "node:24-alpine" in web
    assert "node:24-alpine" in iframe
    assert "node:20-alpine" not in iframe
    assert "pnpm@10.11.0 --registry=https://registry.npmmirror.com" in iframe
    assert "corepack pnpm" not in iframe


def test_frontend_compose_uses_static_runtime_without_hmr():
    """业务联调页面必须走静态服务，避免 HMR 断线后自动整页刷新。"""
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    production = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    iframe = (ROOT / "docker" / "chat-iframe.Dockerfile").read_text(encoding="utf-8")

    assert compose.count("target: production") >= 2
    assert '- "5173:80"' in compose
    assert '- "${CHAT_IFRAME_DEV_HOST_PORT:-5174}:80"' in compose
    assert "pnpm run server" not in compose
    assert "pnpm exec vite --host" not in compose
    assert "./chat-iframe/public/example.html:/usr/share/nginx/html/chat-iframe/example.html:ro" in compose
    assert "FROM ${NGINX_ALPINE_IMAGE} AS production" in iframe
    assert "ARG VITE_MINIO_CONSOLE_URL" in (ROOT / "docker" / "web.Dockerfile").read_text(encoding="utf-8")
    assert "VITE_MINIO_CONSOLE_URL:" in compose
    assert "VITE_MINIO_CONSOLE_URL:" in production
    assert production.count("target: production") >= 2
