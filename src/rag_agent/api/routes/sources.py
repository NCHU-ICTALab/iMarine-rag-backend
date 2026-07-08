"""知識庫來源端點：清單（綜合對話右欄）+ 啟用/停用（設定頁知識庫管理）。"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.queries import list_sources, set_source_enabled
from ..deps import get_session

router = APIRouter(prefix="/sources", tags=["sources"])


class EnabledIn(BaseModel):
    enabled: bool


@router.get("")
async def get_sources(session: AsyncSession = Depends(get_session)) -> list[dict]:
    """列出所有知識庫來源與其 chunk 數、啟用狀態（含 source_type 供前端分類）。"""
    return await list_sources(session)


@router.post("/{source_id}/enabled")
async def toggle_source(
    source_id: str, body: EnabledIn, session: AsyncSession = Depends(get_session)
) -> dict:
    """啟用/停用某來源；停用後檢索不再納入其 chunk。"""
    await set_source_enabled(session, source_id, body.enabled)
    return {"ok": True, "source_id": source_id, "enabled": body.enabled}
