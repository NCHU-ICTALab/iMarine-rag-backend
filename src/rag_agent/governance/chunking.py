import hashlib
import json
import re
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..db.models import Chunk, RawDocument


class BaseChunker(ABC):
    @abstractmethod
    async def to_chunks(self, raw_doc: RawDocument, content: dict) -> list[Chunk]: ...

    async def run(self, session: AsyncSession, raw_doc: RawDocument) -> int:
        """切段並寫入 DB，回傳新增筆數（已存在的 chunk_id 略過）。"""
        content = json.loads(Path(raw_doc.content_pointer).read_bytes())
        chunks = await self.to_chunks(raw_doc, content)

        new_count = 0
        for chunk in chunks:
            result = await session.execute(
                select(Chunk).where(Chunk.chunk_id == chunk.chunk_id)
            )
            if not result.first():
                session.add(chunk)
                new_count += 1

        await session.commit()
        return new_count

    @staticmethod
    def _checksum(text: str) -> str:
        return f"sha256:{hashlib.sha256(text.encode()).hexdigest()}"

    @staticmethod
    def _token_count(text: str) -> int:
        # 中文字約 1 token，英文約 4 chars/token，取粗估
        return max(1, len(text) // 2)


class LawChunker(BaseChunker):
    """商港法：每條為一個 chunk，條內各項保留為段落。"""

    async def to_chunks(self, raw_doc: RawDocument, content: dict) -> list[Chunk]:
        chunks = []
        for article in content["articles"]:
            n = article["article_no"]
            label = article["article_label"]   # e.g. 第12條
            text = article["text"]

            chunks.append(Chunk(
                chunk_id=f"LAW-K0080001-art{n:04d}",
                raw_document_id=raw_doc.id,
                source_id=raw_doc.source_id,
                source_type="regulation",
                title=f"商港法 {label}",
                text=text,
                section_path=label,
                original_url=content["source_url"],
                published_at=None,
                credibility_score=96,
                chunk_index=n,
                token_count=self._token_count(text),
                checksum=self._checksum(text),
            ))
        return chunks


class NewsChunker(BaseChunker):
    """航港局新聞稿：每則新聞抓全文後，依段落切段（250–500 tokens）。"""

    TARGET_TOKENS = 400
    OVERLAP_TOKENS = 60      # ~15%

    async def to_chunks(self, raw_doc: RawDocument, content: dict) -> list[Chunk]:
        chunks: list[Chunk] = []
        async with httpx.AsyncClient(verify=False, timeout=15) as client:
            for entry in content["entries"]:
                article_chunks = await self._chunk_entry(raw_doc, entry, client)
                chunks.extend(article_chunks)
        return chunks

    async def _chunk_entry(
        self, raw_doc: RawDocument, entry: dict, client: httpx.AsyncClient
    ) -> list[Chunk]:
        guid = entry["guid"]
        title = entry["title"]
        url = entry["link"]
        published_at = _parse_dt(entry.get("published_at", ""))

        full_text = await _fetch_article_text(url, client)
        if not full_text:
            full_text = title          # fallback：至少保留標題

        paragraphs = _split_paragraphs(full_text)
        windows = _sliding_windows(paragraphs, self.TARGET_TOKENS, self.OVERLAP_TOKENS)

        chunks = []
        for i, text in enumerate(windows):
            chunk_id = f"NEWS-motcmpb-{guid[:8]}-p{i}"
            chunks.append(Chunk(
                chunk_id=chunk_id,
                raw_document_id=raw_doc.id,
                source_id=raw_doc.source_id,
                source_type="news",
                title=title,
                text=text,
                section_path=f"para-{i}",
                original_url=url,
                published_at=published_at,
                credibility_score=92,
                chunk_index=i,
                token_count=self._token_count(text),
                checksum=self._checksum(text),
            ))
        return chunks


class AltEnergyNewsChunker(BaseChunker):
    """替代能源新聞聚合：每則新聞一個 chunk（標題＋來源＋分類＋關鍵字）。

    連結型聚合無全文，故不抓外站內容；chunk 內容較薄，但關鍵字有助 lexical 檢索。
    chunk_id 綁定新聞 ID，重複 ingest 會自動去重（新聞才會新增）。
    """

    CREDIBILITY = 85         # 外部策展新聞標題，略低於一手法規/新聞全文

    async def to_chunks(self, raw_doc: RawDocument, content: dict) -> list[Chunk]:
        chunks: list[Chunk] = []
        for item in content["items"]:
            title = item.get("title", "").strip()
            item_id = item.get("id")
            if not title or item_id is None:
                continue

            meta_bits = []
            if item.get("source"):
                meta_bits.append(f"來源：{item['source']}")
            if item.get("tags"):
                meta_bits.append(f"分類：{item['tags']}")
            if item.get("keywords"):
                meta_bits.append(f"關鍵字：{item['keywords']}")
            text = title if not meta_bits else title + "\n" + "｜".join(meta_bits)

            chunks.append(Chunk(
                chunk_id=f"AE-NEWS-{item_id}",
                raw_document_id=raw_doc.id,
                source_id=raw_doc.source_id,
                source_type="alt_energy",
                title=title,
                text=text,
                section_path="news",
                original_url=item.get("link", ""),
                published_at=_parse_dt(item.get("published_at", "")),
                credibility_score=self.CREDIBILITY,
                chunk_index=0,
                token_count=self._token_count(text),
                checksum=self._checksum(text),
            ))
        return chunks


class AltEnergyChunker(BaseChunker):
    """替代能源專區：每個 section 依段落切段（與新聞相同的滑動視窗策略）。"""

    TARGET_TOKENS = 400
    OVERLAP_TOKENS = 60      # ~15%
    CREDIBILITY = 88         # 航港局 iMarine 策展內容（次於一手法規/新聞）

    async def to_chunks(self, raw_doc: RawDocument, content: dict) -> list[Chunk]:
        chunks: list[Chunk] = []
        for sec in content["sections"]:
            paragraphs = _split_paragraphs(sec["text"])
            windows = _sliding_windows(paragraphs, self.TARGET_TOKENS, self.OVERLAP_TOKENS)
            for i, text in enumerate(windows):
                chunks.append(Chunk(
                    chunk_id=f"AE-{raw_doc.source_id}-{sec['section_id']}-p{i}",
                    raw_document_id=raw_doc.id,
                    source_id=raw_doc.source_id,
                    source_type="alt_energy",
                    title=sec["title"],
                    text=text,
                    section_path=sec["section_id"],
                    original_url=sec.get("url", content.get("source_url", "")),
                    published_at=None,
                    credibility_score=self.CREDIBILITY,
                    chunk_index=i,
                    token_count=self._token_count(text),
                    checksum=self._checksum(text),
                ))
        return chunks


# ── 工具函式 ──────────────────────────────────────────────────────────────

async def _fetch_article_text(url: str, client: httpx.AsyncClient) -> str:
    try:
        resp = await client.get(url)
        soup = BeautifulSoup(resp.text, "lxml")
        container = soup.select_one("div.content")
        if not container:
            return ""
        return container.get_text(separator="\n", strip=True)
    except Exception:
        return ""


def _split_paragraphs(text: str) -> list[str]:
    """依換行切段，過濾空段。"""
    paras = re.split(r"\n{1,}", text)
    return [p.strip() for p in paras if p.strip()]


def _sliding_windows(
    paragraphs: list[str], target: int, overlap: int
) -> list[str]:
    """
    將段落合併成 target token 大小的視窗，前後重疊 overlap tokens。
    若整篇不超過 target，直接回傳一個視窗。
    """
    if not paragraphs:
        return []

    full = "\n".join(paragraphs)
    if len(full) // 2 <= target:
        return [full]

    windows: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = max(1, len(para) // 2)
        if current_tokens + para_tokens > target and current:
            windows.append("\n".join(current))
            # 保留末尾段落作為下一視窗的 overlap
            overlap_chars = overlap * 2
            tail = "\n".join(current)[-overlap_chars:]
            current = [tail] if tail else []
            current_tokens = max(1, len(tail) // 2)
        current.append(para)
        current_tokens += para_tokens

    if current:
        windows.append("\n".join(current))

    return windows


def _parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    try:
        from datetime import timezone
        dt = datetime.fromisoformat(s)
        # 統一轉成 UTC naive（DB 欄位是 TIMESTAMP WITHOUT TIME ZONE）
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except ValueError:
        return None
