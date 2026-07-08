import uuid
from dataclasses import dataclass, field
from datetime import datetime

from ..indexing.retrieval import RetrievedChunk


@dataclass
class EvidenceItem:
    evidence_id: str
    chunk_id: str
    source_id: str
    source_type: str
    title: str
    text: str
    locator: dict
    source_url: str
    published_at: str | None
    retrieval_score: float
    credibility_score: int


@dataclass
class EvidencePackage:
    evidence_package_id: str
    task_type: str                # "chat_qa" | "report_generation"
    query: str
    evidence_items: list[EvidenceItem]
    conflict_flag: str = "not_evaluated"   # MVP：誠實標記，不偽裝成 none
    confidence: float = 0.0
    policy_verdict: str = "allow"
    generation_instruction: dict = field(default_factory=lambda: {
        "answer_only_from_evidence": True,
        "must_cite": True,
        "state_uncertainty_if_conflict": True,
    })

    def to_dict(self) -> dict:
        return {
            "evidence_package_id": self.evidence_package_id,
            "task_type": self.task_type,
            "query": self.query,
            "evidence_items": [
                {
                    "evidence_id": e.evidence_id,
                    "chunk_id": e.chunk_id,
                    "source_id": e.source_id,
                    "source_type": e.source_type,
                    "title": e.title,
                    "text": e.text,
                    "locator": e.locator,
                    "source_url": e.source_url,
                    "published_at": e.published_at,
                    "retrieval_score": round(e.retrieval_score, 4),
                    "credibility_score": e.credibility_score,
                }
                for e in self.evidence_items
            ],
            "conflict_flag": self.conflict_flag,
            "confidence": round(self.confidence, 4),
            "policy_verdict": self.policy_verdict,
            "generation_instruction": self.generation_instruction,
        }


def build_package(
    query: str,
    chunks: list[RetrievedChunk],
    task_type: str = "chat_qa",
) -> EvidencePackage:
    date_str = datetime.utcnow().strftime("%Y%m%d")
    pkg_id = f"EP-{date_str}-{uuid.uuid4().hex[:4].upper()}"

    items = [
        EvidenceItem(
            evidence_id=f"ev_{i+1:03d}",
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
        for i, c in enumerate(chunks)
    ]

    # 信心分數：前三名 RRF 均值，正規化到 0~1
    top3 = [c.rrf_score for c in chunks[:3]]
    confidence = min(1.0, (sum(top3) / len(top3)) * 30) if top3 else 0.0

    return EvidencePackage(
        evidence_package_id=pkg_id,
        task_type=task_type,
        query=query,
        evidence_items=items,
        confidence=confidence,
    )


def _locator(chunk: RetrievedChunk) -> dict:
    if chunk.source_type == "regulation" and chunk.section_path:
        return {"article": chunk.section_path}
    if chunk.section_path:
        return {"section": chunk.section_path}
    return {}
