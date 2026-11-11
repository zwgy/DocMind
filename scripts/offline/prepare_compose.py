"""在构建机复用生产编排生成离线文件；目标机不需要 Python 或源码。"""

import json
from pathlib import Path
import sys

import yaml


def prepare(source: Path, destination: Path, release: str) -> None:
    """保留生产服务和数据路径，固定镜像并去掉需要构建及外部 OCR 的服务。"""
    config = yaml.safe_load((source / "docker-compose.prod.yml").read_text(encoding="utf-8"))
    config = {"services": config["services"], "networks": config["networks"]}
    config["services"] = {name: service for name, service in config["services"].items() if not service.get("profiles")}
    for name, service in config["services"].items():
        service.pop("build", None)
        service.pop("container_name", None)
        service["platform"] = "linux/amd64"
        service["pull_policy"] = "never"
        if name == "sandbox-provisioner":
            service["image"] = f"yuxi-sandbox-provisioner:{release}"
            service["environment"] = [offline_sandbox_setting(item, release) for item in service["environment"]]
        elif service["image"].startswith("yuxi-"):
            service["image"] = f"{service['image'].split(':')[0]}:{release}"
    config["networks"]["app-network"].pop("name", None)
    config["services"]["web"]["ports"] = ["${WEB_PORT:-80}:80"]
    # 服务间用容器网络访问数据库，无需占用宿主机端口，便于同机部署测试与生产。
    config["services"]["postgres"].pop("ports", None)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "compose.yaml").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    images = sorted({service["image"] for service in config["services"].values()} | {f"yuxi-sandbox-runtime:{release}"})
    (destination / "images.txt").write_text("\n".join(images) + "\n", encoding="utf-8")
    template = (source / ".env.template").read_text(encoding="utf-8")
    template += "\n# 同机测试/生产使用不同项目名和端口，项目名仅用小写字母、数字和连字符。\n"
    template += "COMPOSE_PROJECT_NAME=docmind\nWEB_PORT=80\n"
    (destination / ".env.template").write_text(template, encoding="utf-8")


def offline_sandbox_setting(setting: str, release: str) -> str:
    """动态沙盒必须使用包内镜像，并按实例划分容器和私有网络。"""
    key = setting.split("=", 1)[0]
    overrides = {
        "SANDBOX_IMAGE": f"yuxi-sandbox-runtime:{release}",
        "DOCKER_NETWORK_PREFIX": "${COMPOSE_PROJECT_NAME:?Set COMPOSE_PROJECT_NAME in .env.prod}-sandbox",
        "DOCKER_SANDBOX_PREFIX": "${COMPOSE_PROJECT_NAME:?Set COMPOSE_PROJECT_NAME in .env.prod}-sandbox",
    }
    return f"{key}={overrides[key]}" if key in overrides else setting


if __name__ == "__main__":
    prepare(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
