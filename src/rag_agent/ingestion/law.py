import re

import httpx
from bs4 import BeautifulSoup

from .base import IngestionConnector

LAW_URL = "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=K0080001"
PCODE = "K0080001"


class ShippingPortActConnector(IngestionConnector):
    """商港法本文 — 從全國法規資料庫 HTML 解析 76 條條文。"""

    @property
    def source_id(self) -> str:
        return "law_moj_shipping_port_act"

    async def fetch(self) -> tuple[dict, str]:
        async with httpx.AsyncClient(verify=False, timeout=30) as client:
            resp = await client.get(LAW_URL)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        articles = _parse_articles(soup)
        revised_at = _parse_revised_at(soup)

        content = {
            "source_type": "regulation",
            "source_url": LAW_URL,
            "raw_format": "html_parsed_json",
            "pcode": PCODE,
            "title": "商港法",
            "articles": articles,
        }
        source_version = f"PCode:{PCODE}; revised_at:{revised_at}"
        return content, source_version


def _parse_articles(soup: BeautifulSoup) -> list[dict]:
    articles = []
    for row in soup.find_all("div", class_="row"):
        col_no = row.find("div", class_="col-no")
        col_data = row.find("div", class_="col-data")
        if not col_no or not col_data:
            continue

        article_num = col_no.get_text(strip=True)
        if not re.match(r"^第\s*\d+\s*條", article_num):
            continue

        # 每個 div.line-XXXX 是一個段落（項/款）
        lines = col_data.find_all("div", class_=re.compile(r"^line-"))
        paragraphs = [d.get_text(strip=True) for d in lines if d.get_text(strip=True)]

        # 從條號提取數字，例如「第 12 條」→ 12
        m = re.search(r"(\d+)", article_num)
        article_no = int(m.group(1)) if m else 0

        articles.append({
            "article_no": article_no,
            "article_label": article_num.replace(" ", ""),
            "paragraphs": paragraphs,
            "text": "\n".join(paragraphs),
        })

    return articles


def _parse_revised_at(soup: BeautifulSoup) -> str:
    """嘗試從頁面抓修正日期，找不到回傳 unknown。"""
    for tag in soup.find_all(string=re.compile(r"\d{4}/\d{2}/\d{2}")):
        m = re.search(r"(\d{4}/\d{2}/\d{2})", tag)
        if m:
            return m.group(1).replace("/", "-")
    return "unknown"
