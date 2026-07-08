"""iMarine 替代能源專區接入。

資料來源為前端 SPA 背後的靜態 JSON（`/alternativeenergy/*.json`）。專區依側邊選單
分為數個「項目」，這裡把每個項目登錄成**獨立的知識庫（Source）**，各自可在知識庫
管理啟用/停用，符合「依項目分類、不放同一個知識庫」的需求。

每個 connector 的 fetch() 把該項目底下異質內容（文章 MarkDown / 燃料介紹 / 優缺點
比較 / 港口加注 / HTML 區塊）正規化成統一的 `sections` 清單，交給 AltEnergyChunker
切段，治理層不需要知道原始格式。
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from .base import IngestionConnector

logger = logging.getLogger(__name__)

SITE = "https://imarine.motcmpb.gov.tw"
AE_BASE = f"{SITE}/alternativeenergy"
SOURCE_TYPE = "alt_energy"

# 單次 ingest 內共享已下載的 JSON，避免多個項目 connector 重複下載同一份檔案。
_JSON_CACHE: dict[str, object] = {}


async def _fetch_json(name: str) -> object:
    if name not in _JSON_CACHE:
        async with httpx.AsyncClient(verify=False, timeout=30) as client:
            resp = await client.get(f"{AE_BASE}/{name}.json")
        resp.raise_for_status()
        _JSON_CACHE[name] = resp.json()
    return _JSON_CACHE[name]


def _html_to_text(html: str) -> str:
    """把區塊 HTML 轉純文字，保留清單/段落換行。"""
    soup = BeautifulSoup(html or "", "lxml")
    for li in soup.find_all("li"):
        li.insert_before("- ")
    return soup.get_text(separator="\n", strip=True)


def _article_url(index: str) -> str:
    """article.json 的 Index（如 policy-un）還原成前端路由 URL。"""
    path = index.replace("-", "/", 1)
    return f"{SITE}/#/alternativeenergy/article/{path}"


@dataclass
class Section:
    section_id: str
    title: str
    text: str
    url: str


@dataclass
class AECategory:
    """一個項目（= 一個知識庫）要收錄哪些內容。"""

    source_id: str
    articles: list[str] = field(default_factory=list)   # article.json 的 Index 值
    include_energy: bool = False
    include_compare: bool = False
    include_bunkerport: bool = False
    include_platform: bool = False
    include_intro: bool = False
    main_route: str = "#/alternativeenergy/intro"


# ── 內容組裝 ──────────────────────────────────────────────────────────────

async def _articles_sections(indexes: list[str]) -> list[Section]:
    data = await _fetch_json("article")
    by_index = {a.get("Index", ""): a for a in data}          # type: ignore[union-attr]
    out: list[Section] = []
    for idx in indexes:
        a = by_index.get(idx)
        if not a or not (a.get("MarkDown") or "").strip():
            logger.warning("替代能源文章缺漏或空白，略過：%s", idx)
            continue
        out.append(Section(
            section_id=idx,
            title=a.get("Title", idx),
            text=a["MarkDown"],
            url=_article_url(idx),
        ))
    return out


async def _energy_sections() -> list[Section]:
    data = await _fetch_json("energy")
    out = []
    for i, fuel in enumerate(data):                            # type: ignore[arg-type]
        ename = fuel.get("EName", "")
        title = f"{fuel.get('Name', '')}（{ename}）" if ename else fuel.get("Name", "")
        out.append(Section(
            section_id=f"fuel-{i}",
            title=title,
            text=fuel.get("MarkDown", ""),
            url=f"{SITE}/#/alternativeenergy/fuel/{i}",
        ))
    return out


async def _compare_sections() -> list[Section]:
    data = await _fetch_json("compare")
    url = f"{SITE}/#/alternativeenergy/compare"
    out = []
    for i, fuel in enumerate(data.get("Data", [])):           # type: ignore[union-attr]
        pros = "\n".join(f"- {x}" for x in fuel.get("Advantages", []))
        cons = "\n".join(f"- {x}" for x in fuel.get("Disadvantages", []))
        name = fuel.get("Name", "")
        text = f"### {name} 優缺點分析\n\n**優點**\n{pros}\n\n**缺點**\n{cons}"
        out.append(Section(f"compare-{i}", f"{name} 優缺點", text, url))
    return out


async def _bunkerport_sections() -> list[Section]:
    data = await _fetch_json("bunkerport")
    url = f"{SITE}/#/alternativeenergy/bunkerport"
    out: list[Section] = []
    intro = (data.get("Intro") or "").strip()                 # type: ignore[union-attr]
    if intro:
        out.append(Section("bunkerport-intro", "重要港口替代燃料加注｜總覽", intro, url))
    for i, port in enumerate(data.get("Data", [])):           # type: ignore[union-attr]
        name = port.get("name", f"港口 {i}")
        out.append(Section(
            f"bunkerport-{i}", f"重要港口替代燃料加注｜{name}",
            port.get("markdown", ""), url,
        ))
    return out


async def _html_block_sections(name: str, route: str) -> list[Section]:
    """introduction.json / platform.json：Data 內每個 {Title, Body(HTML)} 為一節。"""
    data = await _fetch_json(name)
    items = data.get("Data", data) if isinstance(data, dict) else data
    url = f"{SITE}/{route}"
    out = []
    for i, item in enumerate(items):
        title = (item.get("Title", "") or "").lstrip("# ").strip() or f"{name}-{i}"
        out.append(Section(f"{name}-{i}", title, _html_to_text(item.get("Body", "")), url))
    return out


async def _build_sections(cat: AECategory) -> list[Section]:
    sections: list[Section] = []
    if cat.include_intro:
        sections += await _html_block_sections("introduction", "#/alternativeenergy/intro")
    if cat.include_energy:
        sections += await _energy_sections()
    if cat.include_compare:
        sections += await _compare_sections()
    if cat.articles:
        sections += await _articles_sections(cat.articles)
    if cat.include_bunkerport:
        sections += await _bunkerport_sections()
    if cat.include_platform:
        sections += await _html_block_sections("platform", "#/alternativeenergy/platform")
    return sections


class AlternativeEnergyConnector(IngestionConnector):
    """單一替代能源項目 → 單一知識庫。以 AECategory 參數化，同一份實作服務所有項目。"""

    def __init__(self, category: AECategory):
        self._cat = category

    @property
    def source_id(self) -> str:
        return self._cat.source_id

    async def fetch(self) -> tuple[dict, str]:
        sections = await _build_sections(self._cat)
        content = {
            "source_type": SOURCE_TYPE,
            "source_url": f"{SITE}/{self._cat.main_route}",
            "raw_format": "json",
            "sections": [
                {"section_id": s.section_id, "title": s.title, "text": s.text, "url": s.url}
                for s in sections
            ],
        }
        version = f"fetched:{datetime.utcnow():%Y-%m-%d}; sections:{len(sections)}"
        return content, version
