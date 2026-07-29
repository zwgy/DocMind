"""离线可视化产物的 Yuxi 适配层。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from yuxi.utils.paths import VIRTUAL_PATH_OUTPUTS

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_ALLOWED_INPUT_DIRS = ("workspace", "uploads", "outputs")
_MAX_SVG_BYTES = 5 * 1024 * 1024
_MAX_REQUEST_BYTES = 64 * 1024


class VisualizationError(ValueError):
    """向模型返回的可执行中文可视化错误。"""


def _renderer_error_detail(stderr: bytes) -> str:
    """提取渲染器真实异常，避免 Node.js 版本尾行覆盖可执行错误。"""
    lines = [line.strip() for line in stderr.decode("utf-8", errors="replace").splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith("Error:") or re.match(r"^[A-Za-z_][\w.]*?(?:Error|Exception):", line):
            return line[:300]
    return (lines[-1] if lines else "渲染器执行失败")[:300]


def _require_input_path(thread_id: str, uid: str, virtual_path: str, suffix: str) -> Path:
    # paths 模块会初始化 agents 包；延迟导入可避免 services 与 toolkits 的注册循环。
    from yuxi.agents.backends.sandbox.paths import resolve_virtual_path

    path = str(virtual_path or "").strip()
    if not path.lower().endswith(suffix):
        raise VisualizationError(f"输入文件必须使用 {suffix} 扩展名")
    if not any(path.startswith(f"/home/gem/user-data/{name}/") for name in _ALLOWED_INPUT_DIRS):
        raise VisualizationError("输入文件只能位于当前会话的 workspace、uploads 或 outputs 目录")
    try:
        actual = resolve_virtual_path(thread_id, path, uid=uid)
    except ValueError as exc:
        # 虚拟路径由模型提供；把底层路径实现细节收敛成可执行的修正提示。
        raise VisualizationError("输入文件路径不合法，请使用当前会话的虚拟路径") from exc
    if not actual.is_file():
        raise VisualizationError("输入文件不存在或不是普通文件")
    if actual.stat().st_size > 10 * 1024 * 1024:
        raise VisualizationError("输入文件超过 10 MB 限制，请先聚合或拆分数据")
    return actual


def _output_directory(thread_id: str, uid: str, output_name: str) -> tuple[Path, str]:
    # 同上：仅在真正操作当前线程目录时才加载沙盒路径适配层。
    from yuxi.agents.backends.sandbox.paths import ensure_thread_dirs, resolve_virtual_path

    if not _NAME_RE.fullmatch(str(output_name or "")):
        raise VisualizationError("output_name 只能使用 1 至 80 个 ASCII 字母、数字、下划线或短横线")
    ensure_thread_dirs(thread_id, uid)
    directory = resolve_virtual_path(thread_id, VIRTUAL_PATH_OUTPUTS, uid=uid)
    return directory, output_name


def _reserve_output(directory: Path, output_name: str) -> tuple[Path, str]:
    for index in range(1, 1000):
        suffix = "" if index == 1 else f"-{index}"
        name = f"{output_name}{suffix}.svg"
        final = directory / name
        try:
            descriptor = os.open(final, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            return final, f"{VIRTUAL_PATH_OUTPUTS}/{name}"
        except FileExistsError:
            continue
    raise VisualizationError("同名产物过多，请更换 output_name")


def _validate_svg(path: Path) -> None:
    data = path.read_bytes()
    if not data or len(data) > _MAX_SVG_BYTES:
        raise VisualizationError("SVG 为空或超过 5 MB 限制")
    text = data.decode("utf-8", errors="strict")
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise VisualizationError("SVG 不能包含 DTD 或实体声明")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise VisualizationError("渲染结果不是格式正确的 SVG") from exc
    if root.tag.rsplit("}", 1)[-1].lower() != "svg":
        raise VisualizationError("渲染结果不是 SVG 根元素")
    if "<script" in lowered or "<foreignobject" in lowered:
        raise VisualizationError("渲染结果不是允许的安全 SVG")
    for element in root.iter():
        for attribute, value in element.attrib.items():
            local_name = attribute.rsplit("}", 1)[-1].lower()
            if local_name.startswith("on"):
                raise VisualizationError("SVG 包含不允许的事件属性")
            if local_name == "href" and not value.startswith("#"):
                raise VisualizationError("SVG 包含不允许的外部资源")
            if local_name == "style" and re.search(r"url\s*\(\s*[\"']?(?!#)", value, flags=re.I):
                raise VisualizationError("SVG 包含不允许的外部样式资源")


async def render_visualization(
    *, thread_id: str, uid: str, script_name: str, request: dict[str, Any], output_name: str
) -> dict[str, str]:
    """以固定本地脚本渲染，并在校验成功后发布一个 SVG 交付物。"""
    directory, normalized_name = _output_directory(thread_id, uid, output_name)
    scripts_dir = Path(__file__).resolve().parents[1] / "agents" / "skills" / "buildin" / "visualization" / "scripts"
    script = scripts_dir / script_name
    if not script.is_file():
        raise VisualizationError("可视化渲染器未安装")
    temporary = directory / f".{normalized_name}.{uuid.uuid4().hex}.tmp"
    final: Path | None = None
    payload = json.dumps(request | {"output": str(temporary)}, ensure_ascii=False).encode("utf-8")
    if len(payload) > _MAX_REQUEST_BYTES:
        raise VisualizationError("渲染请求过大，请减少节点或字段")
    command = ["node", str(script)] if script.suffix == ".mjs" else ["python", str(script)]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(payload), timeout=20)
        if process.returncode != 0:
            raise VisualizationError(f"渲染失败：{_renderer_error_detail(stderr)}")
        summary = json.loads(stdout.decode("utf-8"))
        _validate_svg(temporary)
        final, virtual_path = _reserve_output(directory, normalized_name)
        # os.replace 只在完成校验后发生，用户不会看到半写入的 SVG。
        os.replace(temporary, final)
        return {"artifact_path": virtual_path, "summary": str(summary.get("summary") or "已生成可视化图")}
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise VisualizationError("渲染器返回格式无效") from exc
    except TimeoutError as exc:
        raise VisualizationError("渲染超时，请减少数据点或节点数量") from exc
    finally:
        temporary.unlink(missing_ok=True)
        if final is not None and final.exists() and final.stat().st_size == 0:
            final.unlink(missing_ok=True)


def chart_source_path(thread_id: str, uid: str, value: str) -> Path:
    return _require_input_path(thread_id, uid, value, ".csv")


def flow_source_path(thread_id: str, uid: str, value: str) -> Path:
    return _require_input_path(thread_id, uid, value, ".flow.json")


def mindmap_source_path(thread_id: str, uid: str, value: str) -> Path:
    return _require_input_path(thread_id, uid, value, ".mindmap.md")
