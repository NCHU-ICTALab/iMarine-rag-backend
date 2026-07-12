"""每日排程設定端點（前端「新聞自動更新」設定區）。"""

from fastapi import APIRouter
from pydantic import BaseModel

from ...scheduler import get_status, save_config

router = APIRouter(prefix="/schedule", tags=["schedule"])


class ScheduleIn(BaseModel):
    enabled: bool
    time: str          # "HH:MM"（伺服器本地時間）


@router.get("")
async def get_schedule() -> dict:
    """回傳排程狀態：啟用/時間/上次執行/下次執行。"""
    return get_status()


@router.post("")
async def set_schedule(cfg: ScheduleIn) -> dict:
    """設定每日自動更新的啟用與時間；即時生效。"""
    save_config(cfg.enabled, cfg.time)
    return get_status()
