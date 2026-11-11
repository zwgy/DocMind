"""验证离线交付的真实文件与 Bash 行为，Docker 操作仅记录而不启动业务服务。"""

import hashlib
import json
import os
from pathlib import Path
import pty
import shutil
import subprocess
import sys
import tarfile

import pytest


ROOT = Path(os.environ.get("YUXI_TEST_REPOSITORY_ROOT", Path(__file__).resolve().parents[3]))


def run(command, *, cwd, env=None):
    """保留失败输出，方便定位部署入口的错误步骤。"""
    return subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=False)


def test_generated_compose_contains_all_runtime_images_and_isolates_instances(tmp_path):
    result = run(
        [sys.executable, str(ROOT / "scripts/offline/prepare_compose.py"), str(ROOT), str(tmp_path), "test-release"],
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    config = json.loads((tmp_path / "compose.yaml").read_text())
    assert len(config["services"]) == 15
    for service in config["services"].values():
        assert "build" not in service
        assert "container_name" not in service
        assert service["platform"] == "linux/amd64"
        assert service["pull_policy"] == "never"
    assert config["services"]["api"]["image"] == "yuxi-api:test-release"
    provisioner = config["services"]["sandbox-provisioner"]
    assert provisioner["image"] == "yuxi-sandbox-provisioner:test-release"
    assert "SANDBOX_IMAGE=yuxi-sandbox-runtime:test-release" in provisioner["environment"]
    assert any("DOCKER_SANDBOX_PREFIX=${COMPOSE_PROJECT_NAME:" in item for item in provisioner["environment"])
    assert "name" not in config["networks"]["app-network"]
    assert config["services"]["web"]["ports"] == ["${WEB_PORT:-80}:80"]
    assert "ports" not in config["services"]["postgres"]
    # 当前官方 Neo4j 镜像没有 curl，交付健康检查不能依赖它。
    assert "curl" not in config["services"]["graph"]["healthcheck"]["test"][1]
    images = (tmp_path / "images.txt").read_text().splitlines()
    assert len(images) == len(set(images)) == 11
    assert "yuxi-sandbox-runtime:test-release" in images
    template = (tmp_path / ".env.template").read_text()
    assert "COMPOSE_PROJECT_NAME=docmind" in template
    assert "WEB_PORT=80" in template


@pytest.fixture
def bundle(tmp_path):
    """只模拟 Docker 边界，实际执行 Bash、文件校验、插件安装和配置保留。"""
    package = tmp_path / "交付包 with spaces"
    package.mkdir()
    source = ROOT / "scripts/offline/deploy.sh"
    if source.exists():
        shutil.copyfile(source, package / "deploy.sh")
    (package / "scripts").mkdir()
    shutil.copyfile(ROOT / "scripts/manage.sh", package / "scripts/manage.sh")
    (package / ".env.template").write_text("COMPOSE_PROJECT_NAME=docmind\n")
    (package / ".env.prod").write_text("COMPOSE_PROJECT_NAME=docmind-test\nJWT_SECRET_KEY=keep-me\n")
    (package / "compose.yaml").write_text('{"services":{}}')
    (package / "images.tar").write_text("fake Docker save archive")
    (package / "images.txt").write_text("yuxi-api:test\nyuxi-sandbox-runtime:test\n")
    sandbox_config = package / "docker/sandbox_provisioner"
    sandbox_config.mkdir(parents=True)
    (sandbox_config / "sandbox.env.template").write_text("CHECK_YUXI_SANDBOX_ENV_EXISTS=True\n")
    (package / "bin").mkdir()
    (package / "bin/docker-compose").write_text("#!/bin/sh\necho 'Docker Compose version v2.29.7'\n")
    (package / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(package)}\n"
            for path in sorted(package.rglob("*"))
            if path.is_file() and path.name != ".env.prod"
        )
    )
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$*" >> "$DOCKER_TEST_LOG"\n'
        'if [[ "$1 $2" == "compose version" ]]; then\n'
        '  [[ -x "$DOCKER_CONFIG/cli-plugins/docker-compose" ]] || exit 1\n'
        '  exec "$DOCKER_CONFIG/cli-plugins/docker-compose" version\n'
        'fi\n'
        'if [[ "$1" == "info" ]]; then echo linux/x86_64; fi\n'
        'if [[ "$1 $2" == "image inspect" ]]; then\n'
        '  [[ "$*" != *"${MISSING_IMAGE:-not-missing}"* ]] || exit 1\n'
        '  echo linux/amd64\n'
        'fi\n'
    )
    docker.chmod(0o755)
    env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}", DOCKER_TEST_LOG=str(tmp_path / "docker.log"))
    return package, env


def test_install_adds_missing_plugin_and_starts_without_network_or_build(bundle):
    package, env = bundle
    data = package / "docker/volumes/keep.txt"
    data.parent.mkdir(parents=True)
    data.write_text("existing business data")
    result = run(["bash", "deploy.sh", "install"], cwd=package, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = Path(env["DOCKER_TEST_LOG"]).read_text()
    assert "load -i images.tar" in calls
    assert "--no-build --pull never --wait" in calls
    assert "image inspect yuxi-sandbox-runtime:test" in calls
    assert "--build" not in calls
    assert not any(line.startswith("pull ") for line in calls.splitlines())
    assert data.read_text() == "existing business data"
    assert "JWT_SECRET_KEY=keep-me" in (package / ".env.prod").read_text()
    assert (package / ".docker/cli-plugins/docker-compose").stat().st_mode & 0o111


def test_init_keeps_existing_configuration_without_printing_secrets(bundle):
    package, env = bundle
    before = (package / ".env.prod").read_bytes()
    result = run(["bash", "deploy.sh", "init"], cwd=package, env=env)
    assert result.returncode == 0, result.stderr
    assert (package / ".env.prod").read_bytes() == before
    assert "keep-me" not in result.stdout + result.stderr
    assert not Path(env["DOCKER_TEST_LOG"]).exists()


def test_first_init_generates_private_independent_credentials(bundle):
    package, env = bundle
    (package / ".env.prod").unlink()
    master, slave = pty.openpty()
    process = subprocess.Popen(
        ["bash", "deploy.sh", "init"], cwd=package, env=env, stdin=slave, stdout=slave, stderr=slave
    )
    os.close(slave)
    try:
        os.write(master, b"y\n\n")
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
    finally:
        os.close(master)
        if process.poll() is None:
            process.kill()
    assert process.returncode == 0, output.decode()
    generated = dict(line.split("=", 1) for line in (package / ".env.prod").read_text().splitlines() if "=" in line)
    credentials = [generated[key] for key in ("JWT_SECRET_KEY", "POSTGRES_PASSWORD", "NEO4J_PASSWORD")]
    assert len(set(credentials)) == 3
    assert all(len(value) == 64 and value.encode() not in output for value in credentials)
    assert (package / ".env.prod").stat().st_mode & 0o077 == 0


def test_init_sandbox_configuration_once_and_preserves_local_settings(bundle):
    package, env = bundle
    path = package / "docker/sandbox_provisioner/sandbox.env"
    first = run(["bash", "deploy.sh", "init"], cwd=package, env=env)
    assert first.returncode == 0, first.stderr
    assert path.exists()
    assert path.read_text() == "CHECK_YUXI_SANDBOX_ENV_EXISTS=True\n"
    path.write_text("LOCAL_SETTING=must-survive-upgrade\n")
    second = run(["bash", "deploy.sh", "install"], cwd=package, env=env)
    assert second.returncode == 0, second.stdout + second.stderr
    assert path.read_text() == "LOCAL_SETTING=must-survive-upgrade\n"


def test_install_rejects_damaged_package_before_docker_load(bundle):
    package, env = bundle
    (package / "images.tar").write_text("damaged on USB")
    result = run(["bash", "deploy.sh", "install"], cwd=package, env=env)
    assert result.returncode != 0
    calls = Path(env["DOCKER_TEST_LOG"])
    assert not calls.exists() or "load " not in calls.read_text()
    assert not (package / ".docker/cli-plugins/docker-compose").exists()


def test_install_rejects_missing_dynamic_sandbox_image_before_start(bundle):
    package, env = bundle
    env["MISSING_IMAGE"] = "yuxi-sandbox-runtime:test"
    result = run(["bash", "deploy.sh", "install"], cwd=package, env=env)
    assert result.returncode != 0
    assert "up -d" not in Path(env["DOCKER_TEST_LOG"]).read_text()


def test_release_packager_and_resource_preparation_exist():
    """构建入口必须主动验证无网络加载，不能把开发容器缓存误当成镜像内容。"""
    script = ROOT / "scripts/package-offline.sh"
    assert script.exists(), "联网打包入口尚未实现"
    content = script.read_text()
    assert "--network none" in content
    assert "verify_offline_resources.py" in content
    assert "git archive" in content
    assert "docker save" in content
    assert "v2.29.7" in content
    dockerfile = (ROOT / "docker/api.Dockerfile").read_text()
    assert "prepare_offline_resources.py" in dockerfile


def test_packager_creates_complete_verified_archive_from_git_snapshot(tmp_path):
    """执行完整打包编排，Docker/下载以进程替身隔离；不冒充真实镜像构建验收。"""
    repo = tmp_path / "source"
    files = [
        "scripts/package-offline.sh", "scripts/offline/prepare_compose.py", "scripts/offline/deploy.sh",
        "scripts/manage.sh", "docker-compose.prod.yml", ".env.template", "docs/advanced/offline-deployment.md",
        "docker/sandbox_provisioner/sandbox.env",
    ]
    for relative in files:
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    git_commands = (
        ["init", "-q"],
        ["add", "."],
        ["-c", "user.name=test", "-c", "user.email=test@local", "commit", "-qm", "source"],
    )
    for args in git_commands:
        result = run(["git", *args], cwd=repo)
        assert result.returncode == 0, result.stderr
    (repo / ".env.prod").write_text("DO_NOT_SHIP_THIS_SECRET=local-only\n")
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, subprocess, sys\n"
        "args = sys.argv[1:]\n"
        "state_path = pathlib.Path(os.environ['FAKE_DOCKER_STATE'])\n"
        "state = json.loads(state_path.read_text()) if state_path.exists() else []\n"
        "if args[0] in ('build', 'pull'):\n"
        "    state.append(args[args.index('-t') + 1] if args[0] == 'build' else args[-1])\n"
        "    state_path.write_text(json.dumps(state))\n"
        "elif args[:2] == ['image', 'inspect']:\n"
        "    names = args[2:args.index('--format')] if '--format' in args else args[2:]\n"
        "    if any(name not in state for name in names): sys.exit(1)\n"
        "    print('linux/amd64' if '--format' in args else json.dumps([{'RepoTags': names}]))\n"
        "elif args[0] == 'run' and '/source/scripts/offline/prepare_compose.py' in args:\n"
        "    mounts = dict(args[i + 1].split(':')[:2][::-1] for i, item in enumerate(args) if item == '-v')\n"
        "    command = [sys.executable, mounts['/source'] + '/scripts/offline/prepare_compose.py']\n"
        "    subprocess.check_call(command + [mounts['/source'], mounts['/output'], args[-1]])\n"
        "elif args[0] == 'save':\n"
        "    pathlib.Path(args[args.index('-o') + 1]).write_text('simulated docker archive')\n"
    )
    curl = fake_bin / "curl"
    curl.write_text(
        f"#!{sys.executable}\n"
        "import hashlib, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "content = b'#!/bin/sh\\necho Docker Compose version v2.29.7\\n'\n"
        "if args[args.index('-o') - 1].endswith('.sha256'):\n"
        "    content = (hashlib.sha256(content).hexdigest() + '  docker-compose-linux-x86_64\\n').encode()\n"
        "pathlib.Path(args[args.index('-o') + 1]).write_bytes(content)\n"
    )
    docker.chmod(0o755)
    curl.chmod(0o755)
    env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}", FAKE_DOCKER_STATE=str(tmp_path / "images.json"))
    output = tmp_path / "release with spaces"
    command = ["bash", "scripts/package-offline.sh", "test-release", str(output)]
    result = run(command, cwd=repo, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    package = output / "docmind-offline-test-release"
    assert run(["sha256sum", "--check", "SHA256SUMS"], cwd=package).returncode == 0
    with tarfile.open(output / "docmind-offline-test-release.tar.gz") as archive:
        names = archive.getnames()
        assert "docmind-offline-test-release/bin/docker-compose" in names
        assert "docmind-offline-test-release/images.tar" in names
        assert "docmind-offline-test-release/docker/sandbox_provisioner/sandbox.env.template" in names
        assert "docmind-offline-test-release/docker/sandbox_provisioner/sandbox.env" not in names
        assert not any(name.endswith(".env.prod") for name in names)
    assert len((package / "images.txt").read_text().splitlines()) == 11
    assert "DO_NOT_SHIP_THIS_SECRET" not in (package / ".env.template").read_text()
    repeated = run(command, cwd=repo, env=env)
    assert repeated.returncode != 0
