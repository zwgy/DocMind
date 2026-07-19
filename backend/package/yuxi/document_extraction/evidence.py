"""模型引用与解析原文之间的确定性定位工具。"""

from __future__ import annotations

import unicodedata

_QUOTE_TRANSLATION = str.maketrans({"“": '"', "”": '"', "„": '"', "‟": '"', "‘": "'", "’": "'"})


def find_source_quote(quote: str | None, source_text: str) -> str | None:
    """在原文中定位引用，并返回未经改写的真实原文片段。"""
    quote = (quote or "").strip()
    if not quote:
        return None
    if quote in source_text:
        return quote

    normalized_quote = _normalize(quote)
    # 只对足够长的引用做排版兼容，避免短字符在归一化后误命中原文。
    if len(normalized_quote) < 8:
        return None
    normalized_source, source_indexes = _normalize(source_text, with_indexes=True)
    start = normalized_source.find(normalized_quote)
    if start < 0:
        return None
    end = start + len(normalized_quote) - 1
    return source_text[source_indexes[start] : source_indexes[end] + 1]


def _normalize(value: str, *, with_indexes: bool = False):
    """消除解析排版差异，同时保留原文索引用于回写真实引用。"""
    chars: list[str] = []
    indexes: list[int] = []
    for index, char in enumerate(value):
        normalized = unicodedata.normalize("NFKC", char).translate(_QUOTE_TRANSLATION)
        for normalized_char in normalized:
            if normalized_char.isspace():
                continue
            chars.append(normalized_char)
            indexes.append(index)
    normalized_value = "".join(chars)
    return (normalized_value, indexes) if with_indexes else normalized_value
