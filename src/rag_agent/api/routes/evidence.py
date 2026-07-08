from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...evidence.packaging import build_package
from ...indexing.retrieval import hybrid_retrieve
from ..deps import get_session

router = APIRouter(prefix="/evidence", tags=["evidence"])


class EvidenceRequest(BaseModel):
    query: str
    top_k: int = 8
    task_type: str = "chat_qa"


@router.post("/package")
async def build_evidence_package(
    req: EvidenceRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """檢索 → 打包 EvidencePackage，回傳 JSON（含引用定位）。"""
    chunks = await hybrid_retrieve(session, req.query, top_k=req.top_k)
    package = build_package(req.query, chunks, task_type=req.task_type)
    return package.to_dict()
