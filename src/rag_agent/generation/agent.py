"""對話代理：以 tool-calling 迴圈驅動的聊天機器人。

每一輪：模型看對話歷史 →（規劃迴圈）自己決定要不要呼叫「檢索知識庫」工具、
查什麼（把追問補成完整問題）→ 累積證據 → 最終答案以串流輸出，事實才引用。

規劃迴圈輸出短小的 JSON 決策（穩定好解析），與最終串流答案分離，
讓 gemma-4-E4B 這種小模型也能穩定跑多步檢索。
"""

import json
import logging
import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime

from ..evidence.packaging import EvidenceItem, EvidencePackage, _locator
from ..indexing.retrieval import RetrievedChunk
from .llm import GenerationResult, raw_generate, stream_generate
from .prompts import build_chat_answer_prompt, build_planner_prompt, format_history

logger = logging.getLogger(__name__)

MAX_SEARCHES = 1          # 每輪檢索步數；改用 query rewrite 多查詢 fan-out 取代多步（控延遲）
PLANNER_MAX_TOKENS = 96


@dataclass
class TurnPlan:
    evidence_items: list[EvidenceItem] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    report_topic: str | None = None       # 非 None = 使用者意圖產生一份報告
    report_template: str | None = None    # 報告模版 id（policy_brief / news_digest / free）

    @property
    def searched(self) -> bool:
        return bool(self.search_queries)

    @property
    def wants_report(self) -> bool:
        return self.report_topic is not None


def plan_turn(
    history: list[dict],
    user_msg: str,
    retrieve_fn: Callable[[str], list[RetrievedChunk]],
) -> TurnPlan:
    """規劃迴圈：模型決定要不要查、查什麼，累積去重後的證據。

    retrieve_fn(query) -> list[RetrievedChunk]，由呼叫端注入（處理 async/DB）。
    """
    history_text = format_history(history)
    evidence: dict[str, EvidenceItem] = {}     # chunk_id -> item（保持首次出現順序）
    queries: list[str] = []
    report_topic: str | None = None
    report_template: str | None = None

    def _accumulate(query: str) -> None:
        for chunk in retrieve_fn(query):
            if chunk.chunk_id not in evidence:
                evidence[chunk.chunk_id] = _to_item(chunk, len(evidence) + 1)

    for _ in range(MAX_SEARCHES):
        prompt = build_planner_prompt(
            history_text, user_msg, list(evidence.values()), queries
        )
        raw, _ = raw_generate(prompt, max_new_tokens=PLANNER_MAX_TOKENS, temperature=0.0)
        action, query, template = _parse_action(raw)

        if action == "report":
            report_topic = query or user_msg
            report_template = template
            queries.append(report_topic)
            _accumulate(report_topic)          # 為報告檢索證據
            break

        if action != "search" or not query or query in queries:
            break

        queries.append(query)
        _accumulate(query)

    return TurnPlan(
        evidence_items=list(evidence.values()),
        search_queries=queries,
        report_topic=report_topic,
        report_template=report_template,
    )


def stream_answer(
    history: list[dict],
    user_msg: str,
    evidence_items: list[EvidenceItem],
    max_new_tokens: int = 512,
    temperature: float = 0.0,
) -> Iterator[str]:
    """依累積的證據串流最終回答。"""
    prompt = build_chat_answer_prompt(format_history(history), user_msg, evidence_items)
    yield from stream_generate(prompt, max_new_tokens=max_new_tokens, temperature=temperature)


def cited_ids(answer: str, evidence_items: list[EvidenceItem]) -> list[str]:
    return [e.evidence_id for e in evidence_items if re.search(rf"\[{e.evidence_id}\]", answer)]


def _package_from_items(
    query: str, items: list[EvidenceItem], task_type: str
) -> EvidencePackage:
    """由累積的證據建 EvidencePackage（信心 = 前三名 RRF 均值正規化）。"""
    top3 = [e.retrieval_score for e in items[:3]]
    confidence = min(1.0, (sum(top3) / len(top3)) * 30) if top3 else 0.0
    return EvidencePackage(
        evidence_package_id=f"EP-{datetime.utcnow():%Y%m%d}-{uuid.uuid4().hex[:4].upper()}",
        task_type=task_type,
        query=query,
        evidence_items=items,
        confidence=confidence,
    )


def finalize_turn(
    user_msg: str, plan: TurnPlan, answer: str
) -> tuple[EvidencePackage, GenerationResult]:
    """把一輪代理結果封裝成 EvidencePackage + GenerationResult（純計算，不落 DB/audit）。

    供 FastAPI /chat 端點與 Streamlit UI 共用，確保引用/信心/coverage 算法一致。
    無證據（招呼/閒聊）時 coverage 記為 1.0，代表「本輪無需引用」而非未達標。
    """
    cited = cited_ids(answer, plan.evidence_items)
    all_ids = [e.evidence_id for e in plan.evidence_items]
    coverage = len(cited) / len(all_ids) if all_ids else 1.0

    pkg = _package_from_items(user_msg, plan.evidence_items, "chat_qa")
    result = GenerationResult(
        answer=answer,
        evidence_package_id=pkg.evidence_package_id,
        cited_ids=cited,
        uncited_ids=[i for i in all_ids if i not in cited],
        citation_coverage=coverage,
    )
    return pkg, result


def report_turn(plan: TurnPlan, max_new_tokens: int = 1024, temperature: float = 0.2):
    """依 plan 的報告意圖：用累積證據建 package → 依模版產報告。回傳 (package, ReportResult)。"""
    from .report import generate_report  # 延遲 import 避免載入順序耦合
    from .templates import get_template
    topic = plan.report_topic or ""
    pkg = _package_from_items(topic, plan.evidence_items, "report_generation")
    result = generate_report(
        pkg, template=get_template(plan.report_template), user_prompt=topic,
        max_new_tokens=max_new_tokens, temperature=temperature,
    )
    return pkg, result


def format_report_answer(result) -> str:
    """把報告各章節攤平成 markdown 對話答案（[ev_xxx] 標記保留，前端轉 cite）。"""
    lines = [f"已依您的需求產生報告：{result.topic}", ""]
    for key in (result.section_order or list(result.sections.keys())):
        label = (result.section_labels or {}).get(key, key)
        lines += [f"## {label}", result.sections[key]["text"], ""]
    return "\n".join(lines).strip()


def _parse_action(raw: str) -> tuple[str, str, str | None]:
    """解析規劃器 JSON → (action, query/topic, template)；無法解析時預設 answer。"""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return "answer", "", None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return "answer", "", None
    action = str(obj.get("action", "answer")).strip()
    query = str(obj.get("query") or obj.get("topic") or "").strip()
    template = str(obj["template"]).strip() if obj.get("template") else None
    return action, query, template


def _to_item(c: RetrievedChunk, i: int) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"ev_{i:03d}",
        chunk_id=c.chunk_id,
        source_id=c.source_id,
        source_type=c.source_type,
        title=c.title,
        text=c.text,
        locator=_locator(c),
        source_url=c.original_url,
        published_at=c.published_at.isoformat() if c.published_at else None,
        retrieval_score=c.rrf_score,
        credibility_score=c.credibility_score,
    )
