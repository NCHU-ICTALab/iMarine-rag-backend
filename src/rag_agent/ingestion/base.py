import hashlib
import json
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..config import settings
from ..db.models import RawDocument, Source


class IngestionConnector(ABC):
    """每個資料來源實作這個介面，治理層不需要知道原始格式。"""

    @property
    @abstractmethod
    def source_id(self) -> str: ...

    @abstractmethod
    async def fetch(self) -> tuple[dict, str]:
        """回傳 (content_dict, source_version)。"""
        ...

    async def ingest(self, session: AsyncSession) -> RawDocument | None:
        """
        執行完整 ingestion 流程：
        1. fetch()
        2. checksum 去重
        3. 寫入本地儲存
        4. 寫入 raw_documents
        回傳新建的 RawDocument，若已存在則回傳 None。
        """
        content, source_version = await self.fetch()

        raw_bytes = json.dumps(content, ensure_ascii=False).encode()
        checksum = f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}"

        result = await session.execute(
            select(RawDocument).where(RawDocument.checksum == checksum)
        )
        if result.first():
            return None

        content_pointer = self._save(raw_bytes, checksum)

        doc = RawDocument(
            source_id=self.source_id,
            source_type=content.get("source_type", "unknown"),
            source_url=content.get("source_url", ""),
            raw_format=content.get("raw_format", "json"),
            content_pointer=str(content_pointer),
            fetched_at=datetime.utcnow(),
            source_version=source_version,
            checksum=checksum,
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)
        return doc

    def _save(self, raw_bytes: bytes, checksum: str) -> Path:
        digest = checksum.removeprefix("sha256:")
        dest = settings.raw_dir / self.source_id / f"{digest}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw_bytes)
        return dest


SOURCE_REGISTRY: list[Source] = [
    Source(
        source_id="law_moj_shipping_port_act",
        source_name="全國法規資料庫（商港法）",
        publisher="法務部全國法規資料庫",
        source_type="regulation",
        jurisdiction="TW",
        license_type="government_open_data_v1",
        access_method="HTML_SCRAPE",
        update_frequency="weekly_or_event_driven",
        trust_score=96,
        attribution_required=True,
        full_text_indexing=True,
        phase="MVP",
    ),
    Source(
        source_id="motcmpb_press_release_rss",
        source_name="交通部航港局新聞稿",
        publisher="交通部航港局",
        source_type="news",
        jurisdiction="TW",
        license_type="government_open_data_v1",
        access_method="RSS",
        feed_url="https://www.motcmpb.gov.tw/Information/RSS?SiteId=1&NodeId=15",
        update_frequency="hourly_or_daily",
        trust_score=92,
        attribution_required=True,
        full_text_indexing=True,
        phase="MVP",
    ),
    Source(
        source_id="ae_news",
        source_name="替代能源專區：最新新聞",
        publisher="交通部航港局 iMarine 航港發展資料庫",
        source_type="alt_energy",
        jurisdiction="TW",
        license_type="government_open_data_v1",
        access_method="JSON_API",
        feed_url="https://imarine.motcmpb.gov.tw/#/alternativeenergy/news",
        update_frequency="daily",
        trust_score=85,
        attribution_required=True,
        full_text_indexing=True,
        phase="Phase2",
    ),
    *[
        Source(
            source_id=sid,
            source_name=name,
            publisher="交通部航港局 iMarine 航港發展資料庫",
            source_type="alt_energy",
            jurisdiction="TW",
            license_type="government_open_data_v1",
            access_method="JSON_STATIC",
            feed_url="https://imarine.motcmpb.gov.tw/#/alternativeenergy/intro",
            update_frequency="event_driven",
            trust_score=88,
            attribution_required=True,
            full_text_indexing=True,
            phase="Phase2",
        )
        for sid, name in (
            ("ae_overview", "替代能源專區：專區簡介"),
            ("ae_fuel", "替代能源專區：替代能源介紹"),
            ("ae_intl", "替代能源專區：國際趨勢與發展"),
            ("ae_taiwan", "替代能源專區：臺灣政策與實踐"),
            ("ae_education", "替代能源專區：教育資源"),
        )
    ],
]


async def ensure_sources(session: AsyncSession) -> None:
    """Source Registry 初始化，已存在的不重複寫入。"""
    for src in SOURCE_REGISTRY:
        existing = await session.get(Source, src.source_id)
        if not existing:
            session.add(src)
    await session.commit()
