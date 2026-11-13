"""通过真实初始化进程检查模板边界，不连接模型或业务数据库。"""

import os
from pathlib import Path
import pty
import re
import shutil
import subprocess
import sys

import pytest


ROOT = Path(os.environ.get("YUXI_TEST_REPOSITORY_ROOT", Path(__file__).resolve().parents[3]))
CREDENTIALS = (
    "JWT_SECRET_KEY",
    "YUXI_INSTANCE_ID",
    "SANDBOX_PROVISIONER_TOKEN",
    "POSTGRES_PASSWORD",
    "NEO4J_PASSWORD",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
)


def env_values(path):
    """只读取生效行，重复变量直接报错，避免后项覆盖掩盖初始化错误。"""
    values = {}
    for line in path.read_text().splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if match:
            key, value = match.groups()
            assert key not in values, key
            values[key] = value
    return values


def run_interactive(command, cwd, answers):
    """使用终端执行原脚本，输入回显关闭前不发送任何真实密钥。"""
    master, slave = pty.openpty()
    process = subprocess.Popen(command, cwd=cwd, stdin=slave, stdout=slave, stderr=slave)
    os.close(slave)
    try:
        os.write(master, answers)
        process.wait(timeout=15)
        output = bytearray()
        while True:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            output.extend(chunk)
        return process.returncode, output.decode()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)


@pytest.fixture
def source(tmp_path):
    """仅复制公开模板和初始化入口，避免测试读取真实配置。"""
    target = tmp_path / "配置 with spaces"
    (target / "scripts").mkdir(parents=True)
    for relative in (".env.template", ".env.prod.template", "scripts/manage.sh", "scripts/init.sh"):
        if (ROOT / relative).exists():
            shutil.copyfile(ROOT / relative, target / relative)
    return target


def test_production_initialization_uses_runtime_template_and_never_asks_cloud_key(source):
    (source / ".env.template").write_text("BUILD_ONLY_SENTINEL=must-not-copy\n")
    code, output = run_interactive(["bash", "scripts/manage.sh", "prod", "init"], source, b"y\n\n")
    assert code == 0, output
    values = env_values(source / ".env.prod")
    assert "BUILD_ONLY_SENTINEL" not in values
    assert "SILICONFLOW_API_KEY" not in output
    assert values["YUXI_ENV"] == "production"
    assert len({values[key] for key in CREDENTIALS}) == len(CREDENTIALS)
    for key in CREDENTIALS:
        value = values[key]
        assert len(value) >= 22 and value not in output, key
    assert (source / ".env.prod").stat().st_mode & 0o077 == 0


def test_production_initialization_leaves_existing_file_byte_for_byte(source):
    expected = b"# local settings\r\nJWT_SECRET_KEY=keep-existing\r\nCUSTOM_MODEL_KEY=custom\r\n"
    (source / ".env.prod").write_bytes(expected)
    result = subprocess.run(["bash", "scripts/manage.sh", "prod", "init"], cwd=source, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (source / ".env.prod").read_bytes() == expected
    assert "keep-existing" not in result.stdout


def test_development_can_initialize_without_cloud_credentials(source, tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n")
    docker.chmod(0o755)
    (source / "scripts/pull_image.sh").write_text("#!/bin/sh\nexit 0\n")
    result = subprocess.run(
        ["bash", "scripts/init.sh"],
        cwd=source,
        env=dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}"),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "__REPLACE_ME__" not in (source / ".env").read_text()
    assert "SILICONFLOW_API_KEY" not in env_values(source / ".env")
    assert len(env_values(source / ".env")["JWT_SECRET_KEY"]) == 64


@pytest.mark.parametrize("credential", ["test-local-key", r"test&key|with\slash"])
def test_development_enables_commented_value_in_place_and_preserves_real_value(source, credential):
    content = (source / "scripts/init.sh").read_text()
    helpers = content.split('echo "🚀 Initializing Yuxi project..."')[0]
    expected = "# before\n# SILICONFLOW_API_KEY=\n# after\n"
    (source / ".env").write_text(expected)
    command = helpers + "\nensure_env_var SILICONFLOW_API_KEY '" + credential + "'\n"
    result = subprocess.run(["bash", "-c", command], cwd=source, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (source / ".env").read_text() == expected.replace(
        "# SILICONFLOW_API_KEY=", f"SILICONFLOW_API_KEY={credential}"
    )
    again = subprocess.run(
        ["bash", "-c", helpers + '\nensure_env_var SILICONFLOW_API_KEY "do-not-replace"\n'],
        cwd=source,
        capture_output=True,
        text=True,
    )
    assert again.returncode == 0
    assert env_values(source / ".env")["SILICONFLOW_API_KEY"] == credential


def test_offline_template_contains_only_runtime_settings(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/offline/prepare_compose.py"), str(ROOT), str(tmp_path), "env-test"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    path = tmp_path / ".env.prod.template"
    assert path.exists(), "离线包必须携带生产运行模板"
    template = path.read_text()
    values = env_values(path)
    assert set(values) == {
        *CREDENTIALS,
        "YUXI_ENV",
        "YUXI_CORS_ORIGINS",
        "CHAT_IFRAME_ALLOWED_ORIGINS",
        "COMPOSE_PROJECT_NAME",
        "WEB_PORT",
    }
    assert "# INTRANET_MODEL_API_KEY=" in template
    for obsolete in (
        "INCOMING_OCR_ENGINE",
        "BUSINESS_EXTRACTION_MODEL",
        "YUXI_SUPER_ADMIN",
        "APT_",
        "VITE_",
        "MINERU_",
        "KUBECONFIG",
        "REDIS_HOST_PORT",
    ):
        assert obsolete not in template, obsolete
    assert not (tmp_path / ".env.template").exists()


def test_development_template_keeps_current_ports_and_removes_obsolete_entries():
    values = env_values(ROOT / ".env.template")
    assert values["REDIS_HOST_PORT"] == "6380"
    assert values["MINIO_API_HOST_PORT"] == "9010"
    assert values["MINIO_CONSOLE_HOST_PORT"] == "9011"
    assert values["MILVUS_GRPC_HOST_PORT"] == "19532"
    assert values["MILVUS_HEALTH_HOST_PORT"] == "9092"
    assert values["VITE_MINIO_CONSOLE_URL"] == "http://localhost:9011"
    template = (ROOT / ".env.template").read_text()
    for removed in (
        "INCOMING_OCR_ENGINE",
        "BUSINESS_EXTRACTION_MODEL",
        "YUXI_SUPER_ADMIN",
        "OPENAI_API_BASE",
        "ARK_API_KEY",
        "TOGETHER_API_KEY",
    ):
        assert removed not in template, removed
    assert "MINERU_API_URI" not in values
    assert "APT_MIRROR" not in values
