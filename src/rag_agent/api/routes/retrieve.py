from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...indexing.retrieval import RetrievedChunk, hybrid_retrieve
from ..deps import get_session

router = APIRouter(prefix="/retrieve", tags=["retrieve"])


class RetrieveRequest(BaseModel):
    query: str
    top_k: int = 8
    source_type: Literal["regulation", "news", "alt_energy"] | None = None


class ChunkOut(BaseModel):
    chunk_id: str
    source_type: str
    title: str
    text: str
    section_path: str | None
    original_url: str
    rrf_score: float
    dense_rank: int
    lexical_rank: int


class RetrieveResponse(BaseModel):
    query: str
    chunks: list[ChunkOut]


def _chunk_out(c: RetrievedChunk) -> ChunkOut:
    return ChunkOut(
        chunk_id=c.chunk_id,
        source_type=c.source_type,
        title=c.title,
        text=c.text,
        section_path=c.section_path,
        original_url=c.original_url,
        rrf_score=round(c.rrf_score, 6),
        dense_rank=c.dense_rank,
        lexical_rank=c.lexical_rank,
    )


@router.post("", response_model=RetrieveResponse)
async def retrieve(
    req: RetrieveRequest,
    session: AsyncSession = Depends(get_session),
) -> RetrieveResponse:
    """Hybrid retrieval（Dense + Lexical RRF）。"""
    chunks = await hybrid_retrieve(
        session, req.query, top_k=req.top_k, source_type=req.source_type
    )
    return RetrieveResponse(query=req.query, chunks=[_chunk_out(c) for c in chunks])
