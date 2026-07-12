"""每日排程：零依賴 asyncio 迴圈，到設定時間自動抓新聞 + 重生成晨報。

不引入 APScheduler。每 30 秒檢查一次，到 data/schedule_config.json 設定的
HH:MM（伺服器本地時間）且當日尚未執行時，跑一次 run_news_ingest + build_daily_brief。
設定變更即時生效（每次 tick 重讀 config）。
"""

import asyncio
import json
import logging
from datetime import date, datetime, timedelta

from .config import settings
from .db.session import AsyncSessionLocal
from .generation.daily_brief import build_daily_brief
from .ingestion.pipeline import run_news_ingest

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 30           # 秒
DEFAULT_TIME = "06:30"

_last_run_date: date | None = None
_last_run_at: str | None = None
_last_result: dict | None = None
_task: asyncio.Task | None = None


def _config_path():
    return settings.data_dir / "schedule_config.json"


def _valid_time(t: str) -> str:
    try:
        datetime.strptime(t, "%H:%M")
        return t
    except (ValueError, TypeError):
        return DEFAULT_TIME


def load_config() -> dict:
    path = _config_path()
    if path.exists():
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            return {
                "enabled": bool(d.get("enabled", False)),
                "time": _valid_time(d.get("time", DEFAULT_TIME)),
            }
        except Exception:  # noqa: BLE001
            logger.warning("讀取 schedule_config.json 失敗，改用預設")
    return {"enabled": False, "time": DEFAULT_TIME}


def save_config(enabled: bool, time_str: str) -> dict:
    cfg = {"enabled": bool(enabled), "time": _valid_time(time_str)}
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    _config_path().write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("排程設定已儲存：enabled=%s time=%s", cfg["enabled"], cfg["time"])
    return cfg


def _next_run(cfg: dict) -> str | None:
    if not cfg["enabled"]:
        return None
    now = datetime.now()
    hh, mm = map(int, cfg["time"].split(":"))
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target.isoformat(timespec="minutes")


async def _run_job() -> None:
    global _last_run_at, _last_result
    logger.info("排程觸發：抓新聞 + 重生成晨報")
    try:
        async with AsyncSessionLocal() as session:
            stats = await run_news_ingest(session)
            await build_daily_brief(session)
        _last_result = stats
    except Exception as exc:  # noqa: BLE001
        logger.exception("排程工作失敗")
        _last_result = {"error": str(exc)}
    _last_run_at = datetime.now().isoformat(timespec="seconds")


async def _loop() -> None:
    global _last_run_date
    logger.info("每日排程迴圈啟動（每 %ds 檢查）", CHECK_INTERVAL)
    while True:
        try:
            cfg = load_config()
            if cfg["enabled"]:
                now = datetime.now()
                # 當日僅執行一次：命中目標分鐘且今天還沒跑過
                if now.strftime("%H:%M") == cfg["time"] and _last_run_date != now.date():
                    _last_run_date = now.date()
                    await _run_job()
        except Exception:  # noqa: BLE001
            logger.exception("排程 tick 失敗")
        await asyncio.sleep(CHECK_INTERVAL)


def start() -> None:
    """於 FastAPI startup 呼叫，建立背景迴圈（重複呼叫安全）。"""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


def get_status() -> dict:
    cfg = load_config()
    return {
        "enabled": cfg["enabled"],
        "time": cfg["time"],
        "last_run_at": _last_run_at,
        "last_result": _last_result,
        "next_run": _next_run(cfg),
    }
