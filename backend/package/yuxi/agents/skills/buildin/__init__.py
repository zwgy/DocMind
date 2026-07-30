from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BuiltinSkillSpec:
    slug: str
    source_dir: Path
    description: str = ""
    version: str = "1.0.0"
    tool_dependencies: tuple[str, ...] = ()
    mcp_dependencies: tuple[str, ...] = ()
    skill_dependencies: tuple[str, ...] = ()


_SKILLS_ROOT = Path(__file__).resolve().parent

BUILTIN_SKILLS: list[BuiltinSkillSpec] = [
    BuiltinSkillSpec(
        slug="visualization",
        source_dir=_SKILLS_ROOT / "visualization" / "skills" / "visualization",
        description=(
            "仅当用户未明确可视化类型时读取此 Skill，并选择数据图表、流程图或思维导图子 Skill；"
            "类型明确时直接读取对应子 Skill。"
        ),
        version="2026.07.30",
        skill_dependencies=("data-chart", "flowchart", "mindmap"),
    ),
    BuiltinSkillSpec(
        slug="data-chart",
        source_dir=_SKILLS_ROOT / "visualization" / "skills" / "data-chart",
        description=(
            "用户明确要求数据图表、柱状图、折线图、面积图、饼图或散点图时必须先读取此 Skill；"
            "只用 render_data_chart 生成 SVG，禁止改用文档生成工具或手写 SVG。"
        ),
        version="2026.07.30",
        tool_dependencies=("render_data_chart",),
    ),
    BuiltinSkillSpec(
        slug="flowchart",
        source_dir=_SKILLS_ROOT / "visualization" / "skills" / "flowchart",
        description=(
            "用户明确要求流程图、审批流程或业务流程图时必须先读取此 Skill；"
            "只用 render_flowchart 生成 SVG，禁止改用文档生成工具或手写 SVG。"
        ),
        version="2026.07.30",
        tool_dependencies=("render_flowchart",),
    ),
    BuiltinSkillSpec(
        slug="mindmap",
        source_dir=_SKILLS_ROOT / "visualization" / "skills" / "mindmap",
        description=(
            "用户明确要求思维导图、脑图或 mind map 时必须先读取此 Skill；"
            "第一步直接读取系统列出的此 Skill 路径，禁止先列目录；随后只调用 render_mind_map 生成 SVG，"
            "禁止改用文档生成工具或手写 SVG。"
        ),
        version="2026.07.30",
        tool_dependencies=("render_mind_map",),
    ),
    BuiltinSkillSpec(
        slug="office-export",
        source_dir=_SKILLS_ROOT / "office-export",
        description=(
            "用户要求生成或导出 DOCX、Word、PDF、XLSX、Excel 文件，或要求把当前会话中的图片、"
            "图表、流程图、思维导图插入这些文件时必须先读取此 Skill。"
        ),
        version="2026.07.30",
        tool_dependencies=("export_office_file",),
    ),
    BuiltinSkillSpec(
        slug="image-gen",
        source_dir=_SKILLS_ROOT / "image-gen",
        description="在 Agent 沙盒中生成图片并保存到 outputs，默认支持 Qwen-Image，也可接入其它图片生成接口。",
        version="2026.06.02",
        tool_dependencies=("present_artifacts",),
    ),
    BuiltinSkillSpec(
        slug="deep-research",
        source_dir=_SKILLS_ROOT / "deep-research",
        description="深度研究编排方法论：澄清范围、拆解规划、并行调度子智能体调研、对抗式核验、综合成带引用的结构化报告。",
        version="2026.06.05",
        tool_dependencies=("tavily_search",),
    ),
    BuiltinSkillSpec(
        slug="knowledge-base",
        source_dir=_SKILLS_ROOT / "knowledge-base",
        description="使用 Yuxi 知识库进行检索、打开文档、文档内定位和查看思维导图。",
        version="2026.06.24",
        tool_dependencies=(
            "list_kbs",
            "query_kb",
            "find_kb_document",
            "open_kb_document",
            "get_mindmap",
            "search_file",
        ),
    ),
    BuiltinSkillSpec(
        slug="incoming-document",
        source_dir=_SKILLS_ROOT / "incoming-document",
        description="查询、读取、统计和综合解读已接入系统的来文，并在必要时按附件核验原文。",
        version="2026.07.21",
        tool_dependencies=(
            "search_incoming_documents",
            "read_incoming_document",
            "get_incoming_document_statistics",
            "ask_user_question",
            "present_artifacts",
        ),
    ),
    BuiltinSkillSpec(
        slug="build-risk-ledger",
        source_dir=_SKILLS_ROOT / "build-risk-ledger",
        description="按时间范围汇总多份来文中的风险、管理要求和任务，生成可追溯风险台账。",
        version="2026.07.30",
        tool_dependencies=(
            "search_incoming_documents",
            "read_incoming_document",
            "get_incoming_document_statistics",
            "ask_user_question",
            "export_office_file",
        ),
        skill_dependencies=("office-export",),
    ),
    BuiltinSkillSpec(
        slug="summarize-assessment-actions",
        source_dir=_SKILLS_ROOT / "summarize-assessment-actions",
        description="按时间范围汇总多份来文中的通报、考评和奖惩事项，保留对象、结果、后续要求及依据。",
        version="2026.07.30",
        tool_dependencies=(
            "search_incoming_documents",
            "read_incoming_document",
            "get_incoming_document_statistics",
            "ask_user_question",
            "present_artifacts",
            "export_office_file",
        ),
        skill_dependencies=("office-export",),
    ),
    BuiltinSkillSpec(
        slug="mysql-reporter",
        source_dir=_SKILLS_ROOT / "mysql-reporter",
        description="生成 MySQL 查询报表并生成可视化图表。",
        version="2026.07.30",
        skill_dependencies=("data-chart",),
    ),
]
