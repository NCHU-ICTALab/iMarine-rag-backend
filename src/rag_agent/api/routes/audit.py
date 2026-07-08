from fastapi import APIRouter, Query

from ...audit.recorder import read_logs

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/logs")
async def get_audit_logs(limit: int = Query(default=50, ge=1, le=500)) -> list[dict]:
    """讀取最近 N 筆審計紀錄（JSONL）。"""
    return read_logs(limit=limit)
