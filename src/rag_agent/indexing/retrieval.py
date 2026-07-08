import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .embedding import embed_texts

TOP_CANDIDATES = 40

_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_to_int(s: str) -> int | None:
    """中文數字（1–99，含十位）轉整數；無法解析回 None。"""
    if "十" in s:
        head, _, tail = s.partition("十")
        tens = _CN_DIGIT.get(head, 1) if head else 1
        ones = _CN_DIGIT.get(tail, 0) if tail else 0
        return tens * 10 + ones
    if s and all(c in _CN_DIGIT for c in s):
        val = 0
        for c in s:
            val = val * 10 + _CN_DIGIT[c]
        return val
    return None


def normalize_numerals(query: str) -> str:
    """把法規條號的中文數字統一成阿拉伯數字（第五條→第5條），讓查詢對齊庫內寫法。"""
    def repl(m: re.Match) -> str:
        n = _cn_to_int(m.group(1))
        return f"第{n}{m.group(2)}" if n is not None else m.group(0)
    return re.sub(r"第([零〇一二兩三四五六七八九十]+)([條項款])", repl, query)


@dataclass
class RetrievedChunk:
    chunk_id: str
    source_id: str
    source_type: str
    title: str
    text: str
    section_path: str | None
    original_url: str
    published_at: datetime | None
    credibility_score: int
    dense_rank: int = 0
    lexical_rank: int = 0
    rrf_score: float = 0.0


# 只檢索「已登錄且啟用」的來源（對應停用 source 後不再進 evidence 的需求）
_ENABLED_FILTER = (
    "AND source_id IN (SELECT source_id FROM sources WHERE enabled)"
)


async def hybrid_retrieve(
    session: AsyncSession,
    query: str,
    top_k: int = 8,
    rrf_k: int = 60,
    source_type: str | None = None,
    source_ids: list[str] | None = None,
) -> list[RetrievedChunk]:
    """
    Dense (pgvector cosine) + Lexical (bigram ILIKE) → RRF fusion。
    只回傳啟用中來源的 chunk。source_type 過濾類型；source_ids 限定特定知識庫（產報告選來源用）。
    所有使用者輸入皆以綁定參數傳入，避免 SQL injection。
    """
    query = normalize_numerals(query)          # 條號中文數字→阿拉伯，dense/lexical 皆受益
    qvec = embed_texts([query], normalize=True)[0]
    vec_lit = "[" + ",".join(str(x) for x in qvec) + "]"

    dense_list = await _dense(session, vec_lit, source_type, source_ids)
    lexical_list = await _lexical(session, query, source_type, source_ids)

    return _rrf(dense_list, lexical_list, rrf_k)[:top_k]


async def multi_retrieve(
    session: AsyncSession,
    queries: list[str],
    top_k: int = 8,
    rrf_k: int = 60,
    source_type: str | None = None,
    source_ids: list[str] | None = None,
) -> list[RetrievedChunk]:
    """多查詢檢索：對每個改寫後的查詢各跑一次 hybrid_retrieve，再跨查詢 RRF 合併。

    供 query rewrite（1–3 組互補 keyword）使用；單一查詢時等同 hybrid_retrieve。
    """
    uniq = list(dict.fromkeys(q.strip() for q in queries if q and q.strip()))
    if not uniq:
        return []
    if len(uniq) == 1:
        return await hybrid_retrieve(session, uniq[0], top_k, rrf_k, source_type, source_ids)

    lists = [
        await hybrid_retrieve(session, q, TOP_CANDIDATES, rrf_k, source_type, source_ids)
        for q in uniq
    ]
    return _merge_by_rrf(lists, top_k, rrf_k)


def _merge_by_rrf(
    lists: list[list[RetrievedChunk]], top_k: int, k: int
) -> list[RetrievedChunk]:
    """跨多個已排序結果清單做 RRF 融合（依各清單中的名次），去重取 top_k。"""
    score: dict[str, float] = {}
    obj: dict[str, RetrievedChunk] = {}
    for lst in lists:
        for rank, c in enumerate(lst, start=1):
            score[c.chunk_id] = score.get(c.chunk_id, 0.0) + 1 / (k + rank)
            obj.setdefault(c.chunk_id, c)
    ranked = sorted(obj.values(), key=lambda c: score[c.chunk_id], reverse=True)
    for c in ranked:
        c.rrf_score = score[c.chunk_id]
    return ranked[:top_k]


def _scope_filter(params: dict, source_type: str | None, source_ids: list[str] | None) -> str:
    """組出 source_type / source_ids 的額外過濾條件，並填入綁定參數。"""
    parts = ""
    if source_type:
        parts += " AND source_type = :src_type"
        params["src_type"] = source_type
    if source_ids:
        parts += " AND source_id = ANY(:src_ids)"
        params["src_ids"] = list(source_ids)
    return parts


async def _dense(
    session: AsyncSession, vec_lit: str, source_type: str | None,
    source_ids: list[str] | None = None,
) -> list[dict]:
    params: dict = {"qvec": vec_lit}
    src_filter = _scope_filter(params, source_type, source_ids)
    rows = await session.execute(text(f"""
        SELECT chunk_id, source_id, source_type, title, text,
               section_path, original_url, published_at, credibility_score
        FROM chunks
        WHERE embedding IS NOT NULL {_ENABLED_FILTER} {src_filter}
        ORDER BY embedding <=> CAST(:qvec AS vector)
        LIMIT {TOP_CANDIDATES}
    """), params)
    return [dict(r) for r in rows.mappings()]


async def _lexical(
    session: AsyncSession, query: str, source_type: str | None,
    source_ids: list[str] | None = None,
) -> list[dict]:
    bigrams = _bigrams(query)
    if not bigrams:
        return []
    params: dict = {f"b{i}": b for i, b in enumerate(bigrams)}
    like_parts = " OR ".join(
        f"(title || ' ' || text) ILIKE '%' || :b{i} || '%'"
        for i in range(len(bigrams))
    )
    # 命中 bigram 數作為相關性分數（取代原本無排序的 LIMIT，避免通用詞命中時隨機回傳）
    hit_expr = " + ".join(
        f"(CASE WHEN (title || ' ' || text) ILIKE '%' || :b{i} || '%' THEN 1 ELSE 0 END)"
        for i in range(len(bigrams))
    )
    src_filter = _scope_filter(params, source_type, source_ids)
    rows = await session.execute(text(f"""
        SELECT chunk_id, source_id, source_type, title, text,
               section_path, original_url, published_at, credibility_score,
               ({hit_expr}) AS _hits
        FROM chunks
        WHERE ({like_parts}) {_ENABLED_FILTER} {src_filter}
        ORDER BY _hits DESC
        LIMIT {TOP_CANDIDATES}
    """), params)
    return [dict(r) for r in rows.mappings()]


def _rrf(
    dense: list[dict], lexical: list[dict], k: int
) -> list[RetrievedChunk]:
    chunk_map: dict[str, dict] = {}
    dense_ranks: dict[str, int] = {}
    lexical_ranks: dict[str, int] = {}

    for rank, row in enumerate(dense, start=1):
        cid = row["chunk_id"]
        dense_ranks[cid] = rank
        chunk_map[cid] = row

    for rank, row in enumerate(lexical, start=1):
        cid = row["chunk_id"]
        lexical_ranks[cid] = rank
        chunk_map.setdefault(cid, row)

    results: list[RetrievedChunk] = []
    for cid, row in chunk_map.items():
        dr = dense_ranks.get(cid, TOP_CANDIDATES + 1)
        lr = lexical_ranks.get(cid, TOP_CANDIDATES + 1)
        rrf = 1 / (k + dr) + 1 / (k + lr)
        results.append(RetrievedChunk(
            chunk_id=cid,
            source_id=row["source_id"],
            source_type=row["source_type"],
            title=row["title"],
            text=row["text"],
            section_path=row["section_path"],
            original_url=row["original_url"],
            published_at=row["published_at"],
            credibility_score=row["credibility_score"],
            dense_rank=dr,
            lexical_rank=lr,
            rrf_score=rrf,
        ))

    results.sort(key=lambda r: r.rrf_score, reverse=True)
    return results


def _bigrams(s: str) -> list[str]:
    """中文查詢切成 2-char bigrams，最多取 8 個。"""
    cleaned = s.replace(" ", "").replace("\n", "")
    seen: set[str] = set()
    out: list[str] = []
    for i in range(len(cleaned) - 1):
        b = cleaned[i : i + 2]
        if b not in seen:
            seen.add(b)
            out.append(b)
        if len(out) == 8:
            break
    return out
