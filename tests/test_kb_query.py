"""測試知識庫查詢在連續呼叫、不同 filter 下的穩定性。"""

import pytest

from src.rag_agent.db.session import AsyncSessionLocal
from sqlalchemy import text


async def fetch_kb(src_type: str | None = None, search: str = "") -> list[dict]:
    async with AsyncSessionLocal() as session:
        conditions = ["1=1"]
        if src_type:
            conditions.append(f"source_type = '{src_type}'")
        if search:
            safe = search.replace("'", "''")
            conditions.append(f"(title ILIKE '%{safe}%' OR text ILIKE '%{safe}%')")
        where = " AND ".join(conditions)
        rows = await session.execute(text(f"""
            SELECT chunk_id, source_type, title, section_path,
                   token_count, credibility_score,
                   embedding IS NOT NULL AS has_embedding
            FROM chunks WHERE {where}
            ORDER BY source_type, chunk_index
        """))
        return [dict(r) for r in rows.mappings()]


# ── 基本查詢 ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_all():
    rows = await fetch_kb()
    assert len(rows) > 0, "應有資料"
    for r in rows:
        assert r["source_type"] in ("regulation", "news", "alt_energy", "uploaded")
        assert r["chunk_id"]
        assert r["token_count"] > 0


@pytest.mark.asyncio
async def test_fetch_regulation_only():
    rows = await fetch_kb(src_type="regulation")
    assert len(rows) > 0
    assert all(r["source_type"] == "regulation" for r in rows)


@pytest.mark.asyncio
async def test_fetch_news_only():
    rows = await fetch_kb(src_type="news")
    assert len(rows) > 0
    assert all(r["source_type"] == "news" for r in rows)


@pytest.mark.asyncio
async def test_fetch_with_keyword():
    rows = await fetch_kb(search="停泊")
    assert len(rows) > 0, "「停泊」應能找到結果"
    for r in rows:
        assert r["chunk_id"]


@pytest.mark.asyncio
async def test_fetch_regulation_with_keyword():
    rows = await fetch_kb(src_type="regulation", search="商港")
    assert len(rows) > 0
    assert all(r["source_type"] == "regulation" for r in rows)


@pytest.mark.asyncio
async def test_fetch_no_result():
    rows = await fetch_kb(search="XYZXYZ不存在關鍵字999")
    assert rows == []


# ── 連續切換 filter（重現 UI 操作場景）──────────────────────────────────

@pytest.mark.asyncio
async def test_repeated_filter_switching():
    """模擬使用者快速切換來源類型，確認不會有 event loop / asyncpg 錯誤。"""
    for _ in range(3):
        r1 = await fetch_kb()
        r2 = await fetch_kb(src_type="regulation")
        r3 = await fetch_kb(src_type="news")
        r4 = await fetch_kb(search="港區")
        assert len(r1) >= len(r2) + len(r3)
        assert len(r2) > 0
        assert len(r3) > 0


@pytest.mark.asyncio
async def test_all_chunks_have_embedding():
    rows = await fetch_kb()
    not_embedded = [r["chunk_id"] for r in rows if not r["has_embedding"]]
    assert not_embedded == [], f"以下 chunk 尚未向量化：{not_embedded}"


@pytest.mark.asyncio
async def test_credibility_scores_valid():
    rows = await fetch_kb()
    for r in rows:
        assert 0 <= r["credibility_score"] <= 100, (
            f"{r['chunk_id']} 可信度 {r['credibility_score']} 超出範圍"
        )
