import subprocess
import sys
from pathlib import Path


def test_builtin_agents_register_in_fresh_process():
    """工具包冷启动必须完成，不能因循环导入留下空 Agent 注册表。"""
    backend_dir = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            ("from yuxi.agents.buildin import agent_manager; print(','.join(sorted(agent_manager._classes)))"),
        ],
        cwd=backend_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=True,
    )

    assert "ChatbotAgent" in result.stdout
    assert "SubAgentBackend" in result.stdout
