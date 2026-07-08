"""Query rewrite：用 LLM 把使用者問題改寫成 1–3 組互補的檢索關鍵詞查詢。

回傳清單一律包含「正規化後的原查詢」作為保底，再加上 LLM 產生的關鍵詞變體；
解析失敗時只回原查詢，不中斷檢索。搭配 indexing.retrieval.multi_retrieve 使用。
"""

import json
import logging
import re

from ..indexing.retrieval import normalize_numerals
from . import provider

logger = logging.getLogger(__name__)

_PROMPT = """你是知識庫檢索的關鍵詞產生器。把使用者問題改寫成 1 到 3 個「不同角度」的搜尋查詢，
用於中文航港法規與新聞知識庫檢索。規則：
- 濃縮成關鍵詞，去掉口語與贅字，保留專有名詞與實體。
- 法規條號一律用阿拉伯數字（例：第五條→第5條）。
- 各查詢角度要互補：例如一個用「法規名+條號」，一個用「主題關鍵詞」。
- 只輸出 JSON 字串陣列，例如 ["商港法 第5條","港區 治安 警察"]，不要任何多餘文字。
使用者問題：{q}
JSON："""


def rewrite_queries(query: str, max_variants: int = 3) -> list[str]:
    """回傳去重後的查詢清單：[正規化原查詢, 變體1, 變體2...]，上限 max_variants+1。"""
    base = normalize_numerals(query.strip())
    variants: list[str] = []
    try:
        out = provider.complete(_PROMPT.format(q=query), max_tokens=128, temperature=0.0)
        match = re.search(r"\[.*\]", out, re.DOTALL)
        if match:
            arr = json.loads(match.group(0))
            variants = [normalize_numerals(str(x).strip()) for x in arr if str(x).strip()]
    except Exception as exc:  # noqa: BLE001 — 改寫失敗不應中斷檢索
        logger.warning("query rewrite 失敗，改用原查詢：%s", exc)

    seen: set[str] = set()
    result: list[str] = []
    for q in [base, *variants]:
        if q and q not in seen:
            seen.add(q)
            result.append(q)
    return result[: max_variants + 1]
