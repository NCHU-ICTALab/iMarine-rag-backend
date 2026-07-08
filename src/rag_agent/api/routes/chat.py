"""對話端點：把 retrieve → evidence package → generate 封裝成單一呼叫。

這是 mvp-spec 第 7 節的可選封裝層。刻意「只組合」底層邏輯——代理規劃迴圈
（agent.plan_turn）決定要不要查、查什麼，再串起 evidence 封裝與稽核，
不把檢索/生成邏輯寫死在此端點內，底層端點仍可獨立呼叫與測試。
"""

import asyncio

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from ...audit.recorder import record, record_report
from ...db.session import AsyncSessionLocal
from ...generation import provider
from ...generation.agent import (
    finalize_turn,
    format_report_answer,
    plan_turn,
    report_turn,
    stream_answer,
)
from ...generation.llm import GenerationResult
from ...generation.query_rewrite import rewrite_queries
from ...indexing.retrieval import multi_retrieve

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str          # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []
    top_k: int = 8
    max_new_tokens: int = 512
    temperature: float = 0.2


class ChatResponse(BaseModel):
    answer: str
    searched: bool
    search_queries: list[str]
    evidence_package_id: str
    cited_ids: list[str]
    uncited_ids: list[str]
    citation_coverage: float
    evidence_package: dict
    provider: str      # 實際生成用的供應商（如「Ollama（本地）」），供前端照實顯示
    model: str         # 實際生成用的模型 id（如「gemma3n:e4b」）
    report: dict | None = None   # 代理判定為產報告意圖時的結構化報告（章節/來源），否則 None


def _retrieve_sync(query: str, top_k: int):
    """代理規劃迴圈需要同步的檢索函式；先 query rewrite 成多組 keyword，再多查詢合併。"""
    queries = rewrite_queries(query)

    async def _go():
        async with AsyncSessionLocal() as session:
            return await multi_retrieve(session, queries, top_k=top_k)

    return asyncio.run(_go())


def _report_dict(result) -> dict:
    order = result.section_order or list(result.sections.keys())
    labels = result.section_labels or {}
    return {
        "report_id": result.report_id,
        "topic": result.topic,
        "template_id": result.template_id,
        "sections": [
            {"key": k, "label": labels.get(k, k),
             "text": result.sections[k]["text"], "citations": result.sections[k]["citations"]}
            for k in order
        ],
        "source_list": result.source_list,
        "citation_coverage": result.citation_coverage,
    }


def _run_turn(req: ChatRequest):
    history = [{"role": m.role, "content": m.content} for m in req.history]
    plan = plan_turn(history, req.message, lambda q: _retrieve_sync(q, req.top_k))

    # 代理判定使用者要一份報告 → 走產報告工具，回傳可讀答案 + 結構化報告
    if plan.wants_report:
        pkg, report = report_turn(
            plan, max_new_tokens=max(req.max_new_tokens, 1024), temperature=req.temperature
        )
        record_report(plan.report_topic or req.message, pkg, report)
        all_ids = [e.evidence_id for e in plan.evidence_items]
        result = GenerationResult(
            answer=format_report_answer(report),
            evidence_package_id=pkg.evidence_package_id,
            cited_ids=report.cited_ids,
            uncited_ids=[i for i in all_ids if i not in report.cited_ids],
            citation_coverage=report.citation_coverage,
        )
        return plan, pkg, result, _report_dict(report)

    answer = "".join(
        stream_answer(
            history, req.message, plan.evidence_items,
            max_new_tokens=req.max_new_tokens, temperature=req.temperature,
        )
    )
    pkg, result = finalize_turn(req.message, plan, answer)
    record(req.message, pkg, result)
    return plan, pkg, result, None


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """代理式對話：模型自行決定檢索 / 產報告 → 生成 → citation 檢查 → 稽核。"""
    # 代理迴圈是同步且會阻塞（多次 LLM 呼叫），丟到工作執行緒避免卡住 event loop。
    plan, pkg, result, report = await run_in_threadpool(_run_turn, req)
    cfg = provider.current()
    return ChatResponse(
        answer=result.answer,
        searched=plan.searched,
        search_queries=plan.search_queries,
        evidence_package_id=result.evidence_package_id,
        cited_ids=result.cited_ids,
        uncited_ids=result.uncited_ids,
        citation_coverage=result.citation_coverage,
        evidence_package=pkg.to_dict(),
        provider=cfg.provider,
        model=cfg.model,
        report=report,
    )
