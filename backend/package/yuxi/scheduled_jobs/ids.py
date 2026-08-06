"""定时任务领域的稳定字符串标识生成。"""

from uuid import uuid4

_ALLOWED_PREFIXES = frozenset({"sjb_", "sjc_", "sj_", "sjr_", "ibi_", "sja_"})


def new_scheduled_job_id(prefix: str) -> str:
    """集中生成方案约定的前缀 ID，避免各用例自行拼接导致审计关联不一致。"""
    if prefix not in _ALLOWED_PREFIXES:
        raise ValueError(f"不支持的定时任务 ID 前缀: {prefix}")
    return f"{prefix}{uuid4().hex}"
