"""測試 hybrid retrieval pipeline 的穩定性與結果品質。"""

import pytest

from src.rag_agent.db.session import AsyncSessionLocal
from src.rag_agent.indexing.retrieval import RetrievedChunk, hybrid_retrieve


async def retrieve(query: str, top_k: int = 5, src_type=None) -> list[RetrievedChunk]:
    async with AsyncSessionLocal() as session:
        return await hybrid_retrieve(session, query, top_k=top_k, source_type=src_type)


@pytest.mark.asyncio
async def test_basic_retrieval_returns_results():
    chunks = await retrieve("商港法港區管理")
    assert len(chunks) > 0


@pytest.mark.asyncio
async def test_top_k_respected():
    for k in (3, 5, 8):
        chunks = await retrieve("港區停泊", top_k=k)
        assert len(chunks) <= k


@pytest.mark.asyncio
async def test_rrf_scores_descending():
    chunks = await retrieve("船舶進港手續", top_k=8)
    scores = [c.rrf_score for c in chunks]
    assert scores == sorted(scores, reverse=True), "RRF 分數應由高到低排列"


@pytest.mark.asyncio
async def test_source_type_filter_regulation():
    chunks = await retrieve("停泊規定", top_k=5, src_type="regulation")
    assert all(c.source_type == "regulation" for c in chunks)


@pytest.mark.asyncio
async def test_source_type_filter_news():
    chunks = await retrieve("航港局", top_k=5, src_type="news")
    assert all(c.source_type == "news" for c in chunks)


@pytest.mark.asyncio
async def test_chunk_fields_complete():
    chunks = await retrieve("商港法")
    for c in chunks:
        assert c.chunk_id
        assert c.title
        assert c.text
        assert c.original_url
        assert c.rrf_score > 0


@pytest.mark.asyncio
async def test_repeated_queries_stable():
    """連續查詢不應有 asyncpg event loop 錯誤。"""
    queries = ["停泊", "進港", "港區安全", "航港局", "商港法"]
    for q in queries:
        chunks = await retrieve(q)
        assert len(chunks) > 0, f"查詢「{q}」應有結果"
