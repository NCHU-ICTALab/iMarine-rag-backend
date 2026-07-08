from datetime import datetime

import feedparser
import httpx

from .base import IngestionConnector

MOTCMPB_RSS = "https://www.motcmpb.gov.tw/Information/RSS?SiteId=1&NodeId=15"


class MotcmpbRssConnector(IngestionConnector):
    """交通部航港局新聞稿 RSS。"""

    @property
    def source_id(self) -> str:
        return "motcmpb_press_release_rss"

    async def fetch(self) -> tuple[dict, str]:
        # 用 httpx 繞過政府網站的 SSL 憑證問題，再交給 feedparser 解析
        async with httpx.AsyncClient(verify=False, timeout=30) as client:
            resp = await client.get(MOTCMPB_RSS)
        resp.raise_for_status()

        feed = feedparser.parse(resp.text)
        entries = [_parse_entry(e) for e in feed.entries]
        fetched_at = datetime.utcnow().isoformat()

        content = {
            "source_type": "news",
            "source_url": MOTCMPB_RSS,
            "raw_format": "rss_parsed_json",
            "feed_title": feed.feed.get("title", ""),
            "fetched_at": fetched_at,
            "entries": entries,
        }
        source_version = f"fetched_at:{fetched_at}"
        return content, source_version


def _parse_entry(entry) -> dict:
    published_at = _parse_date(entry)
    return {
        "guid": entry.get("id", ""),
        "title": entry.get("title", ""),
        "link": entry.get("link", ""),
        "summary": entry.get("summary", ""),
        "published_at": published_at,
    }


def _parse_date(entry) -> str:
    # feedparser 提供 published_parsed（time.struct_time）
    if entry.get("published_parsed"):
        return datetime(*entry.published_parsed[:6]).isoformat()
    if entry.get("updated"):
        return entry.updated
    return ""
