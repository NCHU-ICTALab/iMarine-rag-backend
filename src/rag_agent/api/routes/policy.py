"""政策情報中心：從知識庫 live 生成收件匣情報卡。

目前提供「每日晨報」（DailyBrief），由 ae_news 最新新聞經 LLM 綜合而成。
前端 policy provider 的 snapshot() 取這些 live briefs，後端不在時 fallback 回 mock。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ... import scheduler
from ...generation.daily_brief import build_daily_brief, get_daily_brief
from ...ingestion.pipeline import run_news_ingest
from ..deps import get_session

router = APIRouter(prefix="/policy", tags=["policy"])


@router.get("/briefs")
async def list_briefs(
    refresh: bool = False, session: AsyncSession = Depends(get_session)
) -> dict:
    """回傳 live 生成的情報卡清單（目前為每日晨報一則）。

    refresh=True 會強制重新生成（呼叫 LLM，較慢）；預設回快取。
    無新聞或生成失敗時回空清單，前端據此 fallback。
    """
    brief = await get_daily_brief(session, refresh=refresh)
    return {"briefs": [brief] if brief else []}


@router.post("/refresh")
async def refresh_news(session: AsyncSession = Depends(get_session)) -> dict:
    """更新新聞：重抓 ae_news → 重新生成晨報。供前端「更新新聞」按鈕。"""
    stats = await run_news_ingest(session)
    brief = await build_daily_brief(session)
    scheduler.mark_run(stats)   # 手動更新也計入排程狀態的「上次執行」
    return {"stats": stats, "briefs": [brief] if brief else []}
