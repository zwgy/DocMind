"""验证生产环境真正声明定时任务所需的常驻进程。"""

import os
from pathlib import Path

import pytest
import yaml


ROOT = Path(os.environ.get("YUXI_TEST_REPOSITORY_ROOT", Path(__file__).resolve().parents[4]))


@pytest.mark.parametrize("role", ["scheduler", "dispatcher"])
def test_production_compose_runs_and_monitors_scheduled_job_processes(role):
    """生产必须具备生成与派发进程，只有 API 和 Worker 无法触发到期任务。"""
    services = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))["services"]

    assert role in services, f"生产环境缺少定时任务 {role} 服务"
    service = services[role]
    assert service["image"] == services["api"]["image"]
    assert service["command"].split() == [
        "uv",
        "run",
        "--no-sync",
        "--no-dev",
        "python",
        "-m",
        f"yuxi.scheduled_jobs.{role}",
    ]
    assert service["environment"]["POSTGRES_URL"] == services["api"]["environment"]["POSTGRES_URL"]
    assert service["env_file"] == [".env.prod"]
    assert service["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert service["healthcheck"]["test"] == [
        "CMD-SHELL",
        f"uv run --no-sync --no-dev python -m yuxi.scheduled_jobs.healthcheck {role}",
    ]
    assert service["restart"] == "unless-stopped"
