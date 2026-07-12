from datetime import datetime

import httpx

from .base import IngestionConnector

# 替代能源專區「最新新聞」的實際資料來源：SPA 背後的 JSON API（非靜態 .json）。
NEWS_API = "https://imarine.motcmpb.gov.tw/api/news"
NEWS_PAGE = "https://imarine.motcmpb.gov.tw/#/alternativeenergy/news"


class AltEnergyNewsConnector(IngestionConnector):
    """iMarine 替代能源專區「最新新聞」聚合。

    這是連結型新聞聚合：每則只有標題、來源、外部連結與標籤/關鍵字，
    無全文內文，因此不抓外站內容（見 README ⚠️ 選項 A）。
    """

    @property
    def source_id(self) -> str:
        return "ae_news"

    async def fetch(self) -> tuple[dict, str]:
        # 政府網站憑證鏈常有問題，沿用專案慣例 verify=False。
        async with httpx.AsyncClient(verify=False, timeout=30) as client:
            resp = await client.get(NEWS_API)
        resp.raise_for_status()

        raw = resp.json()
        items = [_parse_item(it) for it in raw if it.get("Title")]
        fetched_at = datetime.utcnow().isoformat()

        content = {
            # source_type 用 alt_energy，讓前端右欄自動歸入「替代能源專區」群組。
            "source_type": "alt_energy",
            "source_url": NEWS_PAGE,
            "raw_format": "json_api",
            "fetched_at": fetched_at,
            "items": items,
        }
        source_version = f"fetched_at:{fetched_at}"
        return content, source_version


def _parse_item(it: dict) -> dict:
    return {
        "id": it.get("ID"),
        "title": (it.get("Title") or "").strip(),
        "source": (it.get("Source") or "").strip(),
        "link": (it.get("Link") or "").strip(),
        "tags": (it.get("Tags") or "").strip(),
        "keywords": (it.get("Keywords") or "").strip(),
        "is_overseas": bool(it.get("IsOverseas")),
        "published_at": it.get("CreatedAt") or "",
    }
