"""報告生成端點：NotebookLM 式「選來源 + 輸入需求 + 挑模版 → 產結構化報告」。

retrieve（可限定 source_ids）→ evidence package → 依模版 generate → audit。
模版可擴充（見 generation/templates.py），不寫死四章節。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...audit.recorder import record_report
from ...evidence.packaging import build_package
from ...generation import provider
from ...generation.query_rewrite import rewrite_queries
from ...generation.report import generate_report
from ...generation.templates import get_template, list_templates
from ...indexing.retrieval import multi_retrieve
from ..deps import get_session

router = APIRouter(prefix="/report", tags=["report"])


class ReportRequest(BaseModel):
    prompt: str                                # 報告主題 / 需求
    source_ids: list[str] | None = None        # 限定的知識庫（None = 全部啟用中）
    template: str | None = None                # 模版 id（見 /api/report/templates）
    top_k: int = 12
    max_new_tokens: int = 2048        # 六段模版需更多輸出，避免末段（結論）被截斷
    temperature: float = 0.2


class ReportResponse(BaseModel):
    report_id: str
    topic: str
    template_id: str
    generated_at: str
    sections: list[dict]                       # [{key, label, text, citations}]
    source_list: list[dict]
    citation_coverage: float
    evidence_package: dict
    provider: str
    model: str


@router.get("/templates")
async def templates() -> list[dict]:
    """可用報告模版清單，供前端「產報告」選擇。"""
    return list_templates()


@router.post("", response_model=ReportResponse)
async def make_report(
    req: ReportRequest,
    session: AsyncSession = Depends(get_session),
) -> ReportResponse:
    tmpl = get_template(req.template)
    queries = rewrite_queries(req.prompt)
    chunks = await multi_retrieve(
        session, queries, top_k=req.top_k, source_ids=req.source_ids
    )
    package = build_package(req.prompt, chunks, task_type="report_generation")
    result = generate_report(
        package, template=tmpl, user_prompt=req.prompt,
        max_new_tokens=req.max_new_tokens, temperature=req.temperature,
    )
    record_report(req.prompt, package, result)

    cfg = provider.current()
    order = result.section_order or list(result.sections.keys())
    labels = result.section_labels or {}
    sections = [
        {
            "key": k,
            "label": labels.get(k, k),
            "text": result.sections[k]["text"],
            "citations": result.sections[k]["citations"],
        }
        for k in order
    ]
    return ReportResponse(
        report_id=result.report_id,
        topic=result.topic,
        template_id=result.template_id,
        generated_at=result.generated_at,
        sections=sections,
        source_list=result.source_list,
        citation_coverage=result.citation_coverage,
        evidence_package=package.to_dict(),
        provider=cfg.provider,
        model=cfg.model,
    )
