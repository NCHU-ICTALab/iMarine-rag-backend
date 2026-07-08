"""稽核紀錄：以 PostgreSQL `audit` schema 為主要真相來源，JSONL 作封存匯出。

record()/record_report()/read_logs() 皆為同步介面，內部用獨立 thread 跑
asyncio.run，因此不論呼叫端是同步（Streamlit）或非同步（FastAPI handler，已在
event loop 內）都能安全寫入 DB。
"""

import asyncio
import concurrent.futures
import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from ..config import settings
from ..evidence.packaging import EvidencePackage
from ..generation.llm import GenerationResult
from .models import GenerationRun

logger = logging.getLogger(__name__)

_AUDIT_FILE: Path | None = None


def _audit_path() -> Path:
    global _AUDIT_FILE
    if _AUDIT_FILE is None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _AUDIT_FILE = settings.data_dir / "audit.jsonl"
    return _AUDIT_FILE


def _run(coro):
    """在獨立 thread 跑 asyncio.run，避開 running event loop 衝突。"""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result(timeout=30)


async def _persist(run: GenerationRun) -> None:
    from ..db.session import AsyncSessionLocal, create_tables
    await create_tables()
    async with AsyncSessionLocal() as session:
        session.add(run)
        await session.commit()


def _archive_jsonl(entry: dict) -> None:
    with _audit_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def record(query: str, package: EvidencePackage, result: GenerationResult) -> dict:
    run = GenerationRun(
        query=query,
        task_type=package.task_type,
        evidence_package_id=package.evidence_package_id,
        confidence=round(package.confidence, 4),
        conflict_flag=package.conflict_flag,
        evidence_count=len(package.evidence_items),
        cited_ids=result.cited_ids,
        citation_coverage=round(result.citation_coverage, 4),
        answer_length=len(result.answer),
    )
    _run(_persist(run))
    entry = _to_entry(run, uncited_ids=result.uncited_ids)
    _archive_jsonl(entry)
    logger.info("Audit recorded: %s", package.evidence_package_id)
    return entry


def record_report(query: str, package: EvidencePackage, report) -> dict:
    """報告生成的稽核紀錄（report 為 generation.report.ReportResult）。"""
    run = GenerationRun(
        query=query,
        task_type="report_generation",
        evidence_package_id=package.evidence_package_id,
        report_id=report.report_id,
        confidence=round(package.confidence, 4),
        conflict_flag=package.conflict_flag,
        evidence_count=len(package.evidence_items),
        cited_ids=report.cited_ids,
        citation_coverage=round(report.citation_coverage, 4),
    )
    _run(_persist(run))
    entry = _to_entry(run)
    _archive_jsonl(entry)
    logger.info("Audit recorded (report): %s", report.report_id)
    return entry


def read_logs(limit: int = 50) -> list[dict]:
    """從 audit.generation_runs 讀取最近紀錄；DB 不可用時回退 JSONL。"""
    try:
        return _run(_read_db(limit))
    except Exception as exc:  # noqa: BLE001 — 稽核讀取失敗不應中斷 UI
        logger.warning("Audit DB read failed (%s), falling back to JSONL", exc)
        return _read_jsonl(limit)


async def _read_db(limit: int) -> list[dict]:
    from ..db.session import AsyncSessionLocal, create_tables
    await create_tables()
    async with AsyncSessionLocal() as session:
        rows = await session.execute(text("""
            SELECT ts, query, task_type, evidence_package_id, report_id,
                   confidence, conflict_flag, evidence_count,
                   cited_ids, citation_coverage, answer_length
            FROM audit.generation_runs
            ORDER BY ts DESC
            LIMIT :limit
        """), {"limit": limit})
        out = []
        for r in rows.mappings():
            d = dict(r)
            d["ts"] = d["ts"].isoformat() + "Z" if d["ts"] else ""
            out.append(d)
        # 對齊舊介面：呼叫端以新到舊逐筆處理
        return list(reversed(out))


def _read_jsonl(limit: int) -> list[dict]:
    path = _audit_path()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    records = [json.loads(ln) for ln in lines if ln]
    return records[-limit:]


def _to_entry(run: GenerationRun, uncited_ids: list[str] | None = None) -> dict:
    entry = {
        "ts": run.ts.isoformat() + "Z",
        "query": run.query,
        "task_type": run.task_type,
        "evidence_package_id": run.evidence_package_id,
        "confidence": run.confidence,
        "conflict_flag": run.conflict_flag,
        "evidence_count": run.evidence_count,
        "cited_ids": run.cited_ids,
        "citation_coverage": run.citation_coverage,
        "answer_length": run.answer_length,
    }
    if run.report_id:
        entry["report_id"] = run.report_id
    if uncited_ids is not None:
        entry["uncited_ids"] = uncited_ids
    return entry
