"""
Danbooru 本地查找和模糊匹配
"""
import csv
import math
import re
from pathlib import Path
from typing import List, Dict
from difflib import SequenceMatcher

BASE_PATH = Path(__file__).parent
DANBOORU_CSV = BASE_PATH / "models" / "danbooru_20250919.csv"

_cache = {
    "characters": None,  # List[Dict] with keys: name, aliases
}


def load_danbooru_characters() -> List[Dict]:
    """
    加载 Danbooru CSV 中的角色信息。
    返回 List[Dict]，每项包含: name, aliases, post_count
    """
    if _cache["characters"] is not None:
        return _cache["characters"]

    if not DANBOORU_CSV.exists():
        print(f"警告: Danbooru CSV 不存在: {DANBOORU_CSV}")
        return []

    characters = []
    try:
        with open(DANBOORU_CSV, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) < 4:
                    continue
                name = row[0].strip()
                # category = row[1]  # Should be 4
                try:
                    post_count = float(row[2])
                except (ValueError, IndexError):
                    continue
                aliases_raw = row[3] if len(row) > 3 else ""
                aliases = [a.strip() for a in aliases_raw.split(",") if a.strip()]

                characters.append({
                    "name": name,
                    "aliases": aliases,
                    "post_count": post_count,
                })
    except Exception as e:
        print(f"加载 Danbooru CSV 失败: {e}")
        return []

    _cache["characters"] = characters
    return characters


def _similarity_score(a: str, b: str) -> float:
    """计算两个字符串的相似度（0-1）"""
    a_lower = a.lower().replace("_", " ").strip()
    b_lower = b.lower().replace("_", " ").strip()
    return SequenceMatcher(None, a_lower, b_lower).ratio()


def _normalize_text(s: str) -> str:
    s = (s or "").lower().strip()
    s = s.replace("_", " ")
    s = re.sub(r"\s+", " ", s)
    return s


def _tag_base_name(tag: str) -> str:
    """nagisa_(blue_archive) -> nagisa"""
    t = _normalize_text(tag)
    return t.split("(", 1)[0].strip()


def _display_tag(tag: str) -> str:
    """给 LLM 的展示格式：去下划线。"""
    return (tag or "").replace("_", " ").strip()


def _lexical_score(query: str, candidate: str) -> float:
    q = _normalize_text(query)
    c = _normalize_text(candidate)
    if not q or not c:
        return 0.0

    # 1) 精确匹配
    if q == c:
        return 1.0

    # 2) 与基础名精确匹配（简称 -> 全称 tag）
    base = _tag_base_name(c)
    if q == base:
        return 0.98

    # 3) 前缀匹配（简称常见）
    if c.startswith(q):
        return 0.92
    if base.startswith(q):
        return 0.9

    # 4) 单词边界包含
    if f" {q}" in c or c.endswith(f" {q}") or f"{q} " in c:
        return 0.82
    if f" {q}" in base or base.endswith(f" {q}") or f"{q} " in base:
        return 0.8

    # 5) 退化到编辑相似度
    seq1 = SequenceMatcher(None, q, c).ratio()
    seq2 = SequenceMatcher(None, q, base).ratio()
    return max(seq1, seq2)


def search_similar_characters(query: str, limit: int = 10) -> List[Dict]:
    """
    模糊查找与 query 相似的角色（最多 limit 个）。
    返回 List[Dict]：[{"name": "...", "aliases": [...], "score": 0.8, ...}, ...]
    按相似度倒序排列。
    """
    characters = load_danbooru_characters()
    if not characters:
        return []

    query_lower = _normalize_text(query)
    if not query_lower:
        return []

    scored = []

    for char in characters:
        tag = char["name"]
        aliases = char.get("aliases") or []

        # 与角色名/基础名的词法匹配（支持简称 -> 全称tag）
        name_score = _lexical_score(query_lower, tag)

        # 与别名的最高匹配
        alias_score = 0.0
        if aliases:
            alias_score = max(_lexical_score(query_lower, a) for a in aliases)

        # 综合文本分
        text_score = max(name_score, alias_score)

        # 热度分：对数归一，避免热门值过度碾压
        post_count = float(char.get("post_count", 0) or 0)
        pop_score = min(1.0, math.log10(max(1.0, post_count)) / 5.0)

        # 最终分：文本为主，热度次之
        score = text_score * 0.84 + pop_score * 0.16

        # 仅保留综合分 >= 0.8 的高置信候选
        if score >= 0.8:
            scored.append({
                "name": tag,
                "display_name": _display_tag(tag),
                "aliases": aliases,
                "post_count": post_count,
                "score": round(score, 3),
                "text_score": round(text_score, 3),
            })

    # 按综合分 -> 文本分 -> 热度排序
    scored.sort(key=lambda x: (-x["score"], -x["text_score"], -x["post_count"]))
    return scored[:limit]


def reload_cache():
    """清空缓存，强制重新加载"""
    _cache["characters"] = None
