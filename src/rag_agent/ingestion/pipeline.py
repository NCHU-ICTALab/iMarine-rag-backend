"""可重用的完整 ingestion pipeline：抓取 → chunking → embedding。

供 FastAPI 的 /ingest/run 端點與 Streamlit 知識庫管理分頁共用同一條邏輯。
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import create_tables
from ..governance.chunking import (
    AltEnergyChunker, AltEnergyNewsChunker, LawChunker, NewsChunker,
)
from ..indexing.embedding import embed_pending
from .alt_energy import AECategory, AlternativeEnergyConnector
from .base import ensure_sources
from .law import ShippingPortActConnector
from .news_imarine import AltEnergyNewsConnector
from .rss import MotcmpbRssConnector

logger = logging.getLogger(__name__)

# 替代能源專區各項目 → 各自的知識庫（source_id）與收錄內容
_AE_CATEGORIES = [
    AECategory("ae_overview", include_intro=True, main_route="#/alternativeenergy/intro"),
    AECategory("ae_fuel", articles=["energy-analysis"],
               include_energy=True, include_compare=True, main_route="#/alternativeenergy/fuel/0"),
    AECategory("ae_intl",
               articles=["policy-un", "policy-imo", "policy-eu",
                         "development-talent", "cooperation-greenshippingcorridor"],
               include_bunkerport=True, main_route="#/alternativeenergy/article/policy/imo"),
    AECategory("ae_taiwan",
               articles=["domestic-policy", "domestic-law", "development-port",
                         "development-sailor", "development-ship",
                         "company-evergreen", "company-yangming", "company-wanhai"],
               include_platform=True, main_route="#/alternativeenergy/article/domestic/policy"),
    AECategory("ae_education", articles=["education-seminar", "education-course"],
               main_route="#/alternativeenergy/article/education/seminar"),
]

# (connector 實例, chunker 實例) 對照
PIPELINE: list[tuple] = [
    (ShippingPortActConnector(), LawChunker()),
    (MotcmpbRssConnector(), NewsChunker()),
    (AltEnergyNewsConnector(), AltEnergyNewsChunker()),
    *[(AlternativeEnergyConnector(cat), AltEnergyChunker()) for cat in _AE_CATEGORIES],
]


async def run_full_ingest(session: AsyncSession) -> dict:
    """觸發所有 connector 抓取 → chunking → embed 待處理 chunks，回傳統計。"""
    await create_tables()
    await ensure_sources(session)

    docs_fetched = 0
    chunks_added = 0
    for connector, chunker in PIPELINE:
        raw_doc = await connector.ingest(session)
        if raw_doc is not None:
            docs_fetched += 1
            added = await chunker.run(session, raw_doc)
            chunks_added += added

    chunks_embedded = await embed_pending(session)
    return {
        "docs_fetched": docs_fetched,
        "chunks_added": chunks_added,
        "chunks_embedded": chunks_embedded,
    }


async def run_news_ingest(session: AsyncSession) -> dict:
    """只抓取替代能源新聞 → chunk → embed（供前端「更新新聞」按鈕，不重跑其他來源）。

    embedding 為 best-effort：即使失敗（如向量維度未對齊需 reembed）也不影響
    新聞抓取與晨報生成，僅回報 chunks_embedded=-1 供上游判斷。
    """
    await create_tables()
    await ensure_sources(session)

    connector, chunker = AltEnergyNewsConnector(), AltEnergyNewsChunker()
    raw_doc = await connector.ingest(session)
    chunks_added = await chunker.run(session, raw_doc) if raw_doc is not None else 0
    try:
        chunks_embedded = await embed_pending(session)
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        logger.warning("news embed 失敗（向量可能需 reembed 對齊維度）：%s", exc)
        chunks_embedded = -1
    return {
        "docs_fetched": 1 if raw_doc is not None else 0,
        "chunks_added": chunks_added,
        "chunks_embedded": chunks_embedded,
    }
