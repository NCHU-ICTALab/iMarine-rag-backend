"""每日海運晨報：從 ae_news 知識庫的最新新聞，用 LLM 綜合成 DailyBrief。

對齊前端 policy 收件匣的 DailyBrief 契約（items[]{text,cite} + watch + sources + qa）：
- items[].text 為一句話重點（純文字），items[].cite 對應 sources[].no。
- 產出快取於 data/daily_brief.json，避免每次讀取都呼叫 LLM。
"""

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text as sql
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from . import provider

logger = logging.getLogger(__name__)

NEWS_SOURCE_ID = "ae_news"
MAX_NEWS = 12                       # 餵給 LLM 的最新新聞數
CACHE_NAME = "daily_brief.json"

SYSTEM = (
    "你是臺灣交通部航港局的海運政策情報分析師。"
    "只能根據使用者提供的新聞標題撰寫每日晨報，不得杜撰未提供的事實或數字。"
)


def _cache_path() -> Path:
    return settings.data_dir / CACHE_NAME


async def _latest_news_items(session: AsyncSession, limit: int) -> list[dict]:
    """讀最新一筆 ae_news 原文的新聞清單。"""
    row = (await session.execute(sql(
        "SELECT content_pointer FROM raw_documents WHERE source_id = :sid "
        "ORDER BY fetched_at DESC LIMIT 1"
    ), {"sid": NEWS_SOURCE_ID})).first()
    if not row or not row[0]:
        return []
    try:
        content = json.loads(Path(row[0]).read_bytes())
    except (OSError, ValueError):
        return []
    return content.get("items", [])[:limit]


def _build_prompt(items: list[dict]) -> str:
    lines = []
    for i, it in enumerate(items, 1):
        meta = " · ".join(x for x in [it.get("source"), it.get("tags"), it.get("keywords")] if x)
        lines.append(f"[{i}] {it.get('title', '')}" + (f"（{meta}）" if meta else ""))
    news_block = "\n".join(lines)
    return (
        "以下是今日蒐集到的替代能源與航運新聞（每則含編號）：\n\n"
        f"{news_block}\n\n"
        "請綜合成一份給航港決策者看的『海運晨報』，挑出 4-6 條最值得關注的重點、"
        "一句『建議關注』的觀察，並提出 2-3 個決策者可能想進一步追問、且可由上述新聞回答的問題。\n"
        "嚴格只輸出 JSON 物件，格式如下：\n"
        '{"items": [{"text": "一句話重點", "cite": 1}], "watch": "建議關注的一句話", '
        '"questions": ["建議追問一", "建議追問二"]}\n'
        "規則：text 為一句話（純文字，不要在文字內放編號或方括號）；"
        "cite 為該重點依據的新聞編號整數（只能引用上方清單內的編號）；"
        "watch 為一句話；questions 為 2-3 個問句字串。不要輸出 JSON 以外的任何文字。"
    )


def _clean(t: str) -> str:
    """去掉 LLM 可能夾帶的 [n] 標記，並跳脫角括號（前端以 innerHTML 插入）。"""
    t = re.sub(r"\[\d+\]", "", str(t)).strip()
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _parse(raw: str, n_items: int) -> tuple[list[dict], str, list[str]]:
    cleaned = re.sub(r"```(?:json)?", "", raw).strip()
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not m:
        return [], "", []
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return [], "", []
    out: list[dict] = []
    for it in obj.get("items", []):
        txt = _clean(it.get("text", ""))
        try:
            cite = int(it.get("cite"))
        except (TypeError, ValueError):
            cite = 0
        if txt:
            out.append({"text": txt, "cite": cite})
    questions = [_clean(q) for q in obj.get("questions", []) if _clean(q)][:3]
    return out, _clean(obj.get("watch", "")), questions


def _assemble(
    items_src: list[dict], parsed: list[dict], watch: str, questions: list[str]
) -> dict | None:
    """把 LLM 產出組成 DailyBrief；只保留有效引用的重點，sources 重新編號對齊 cite。"""
    produced = len(parsed)
    grounded = [it for it in parsed if 1 <= it["cite"] <= len(items_src)]
    if not grounded:
        return None

    used = sorted({it["cite"] for it in grounded})
    renum = {old: new for new, old in enumerate(used, 1)}
    sources = [
        {
            "no": renum[old],
            "name": items_src[old - 1].get("title", ""),
            "cat": "海運焦點新聞",
            "date": (items_src[old - 1].get("published_at") or "")[:10],
            "checked": True,
        }
        for old in used
    ]
    items = [{"text": it["text"], "cite": renum[it["cite"]]} for it in grounded]

    now_tw = datetime.utcnow() + timedelta(hours=8)          # 顯示用臺北時間
    grounding = round(100 * len(grounded) / produced) if produced else 0
    return {
        "id": "day-live",
        "type": "daily",
        "title": f"{now_tw.strftime('%m-%d')} 海運晨報",
        "time": f"今日 {now_tw.strftime('%H:%M')}",
        "grounding": grounding,
        "groundingNote": f"{len(grounded)} / {produced} 條重點可追溯新聞來源",
        "retrieved": len(items_src),
        "items": items,
        "watch": {"text": watch or "持續追蹤替代燃料與國際航運政策動態"},
        "sources": sources,
        # 建議追問（a 留空：晨報為 live 卡，chip 點擊走真後端 /api/chat 而非預錄劇本）
        "qa": [{"q": q, "a": ""} for q in questions],
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }


async def build_daily_brief(session: AsyncSession) -> dict | None:
    """生成晨報並寫入快取；新聞不足或 LLM 失敗時回 None。"""
    items_src = await _latest_news_items(session, MAX_NEWS)
    if not items_src:
        logger.info("daily_brief: 無 ae_news 新聞，略過生成")
        return None

    raw = provider.complete(_build_prompt(items_src), max_tokens=1024, temperature=0.3, system=SYSTEM)
    parsed, watch, questions = _parse(raw, len(items_src))
    brief = _assemble(items_src, parsed, watch, questions)
    if brief is None:
        logger.warning("daily_brief: LLM 產出無有效引用重點")
        return None

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    _cache_path().write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    return brief


def _read_cache() -> dict | None:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


async def get_daily_brief(session: AsyncSession, refresh: bool = False) -> dict | None:
    """取得晨報：預設回快取，無快取或 refresh=True 時重新生成。"""
    if not refresh:
        cached = _read_cache()
        if cached is not None:
            return cached
    return await build_daily_brief(session)
