from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...ingestion.pipeline import run_full_ingest
from ..deps import get_session

router = APIRouter(prefix="/ingest", tags=["ingest"])


class IngestResult(BaseModel):
    docs_fetched: int
    chunks_added: int
    chunks_embedded: int


@router.post("/run", response_model=IngestResult)
async def run_ingestion(session: AsyncSession = Depends(get_session)) -> IngestResult:
    """觸發所有 connector 抓取資料 → chunking → embed 待處理 chunks。"""
    stats = await run_full_ingest(session)
    return IngestResult(**stats)


@router.get("/status")
async def ingest_status(session: AsyncSession = Depends(get_session)) -> dict:
    """回傳目前 DB 中文件與 chunk 統計。"""
    counts = await session.execute(
        text("SELECT source_type, COUNT(*) FROM chunks GROUP BY source_type")
    )
    rows = {r[0]: r[1] for r in counts}
    total = await session.execute(text("SELECT COUNT(*) FROM chunks"))
    embedded = await session.execute(
        text("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL")
    )
    return {
        "chunks_by_type": rows,
        "total_chunks": total.scalar(),
        "embedded_chunks": embedded.scalar(),
    }
