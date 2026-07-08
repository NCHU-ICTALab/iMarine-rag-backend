from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...audit.recorder import record
from ...evidence.packaging import build_package
from ...generation.llm import generate
from ...generation.query_rewrite import rewrite_queries
from ...indexing.retrieval import multi_retrieve
from ..deps import get_session

router = APIRouter(prefix="/generate", tags=["generate"])


class GenerateRequest(BaseModel):
    query: str
    top_k: int = 8
    max_new_tokens: int = 768
    temperature: float = 0.2
    task_type: str = "chat_qa"


class GenerateResponse(BaseModel):
    answer: str
    evidence_package_id: str
    cited_ids: list[str]
    uncited_ids: list[str]
    citation_coverage: float
    evidence_package: dict


@router.post("", response_model=GenerateResponse)
async def generate_answer(
    req: GenerateRequest,
    session: AsyncSession = Depends(get_session),
) -> GenerateResponse:
    """完整 pipeline：retrieve → evidence package → LLM generate → audit record。"""
    chunks = await multi_retrieve(session, rewrite_queries(req.query), top_k=req.top_k)
    package = build_package(req.query, chunks, task_type=req.task_type)
    result = generate(package, max_new_tokens=req.max_new_tokens, temperature=req.temperature)
    record(req.query, package, result)

    return GenerateResponse(
        answer=result.answer,
        evidence_package_id=result.evidence_package_id,
        cited_ids=result.cited_ids,
        uncited_ids=result.uncited_ids,
        citation_coverage=result.citation_coverage,
        evidence_package=package.to_dict(),
    )
