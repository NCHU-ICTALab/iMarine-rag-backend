"""Embedding：可設定的向量化後端（本地 sentence-transformers 或 OpenAI 相容 API）。

設定存於 data_dir/embed_config.json，跨程序保存。更換模型後維度可能改變，需呼叫
reembed_all() 重新編碼全部 chunk（會自動調整向量欄位維度並重建 HNSW 索引）。
"""

import json
import logging
import math
from dataclasses import asdict, dataclass

import httpx
from sqlalchemy import text as sqltext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ..config import settings
from ..db.models import Chunk

logger = logging.getLogger(__name__)

BATCH_SIZE = 64


@dataclass
class EmbedConfig:
    backend: str          # "local" | "api"
    model: str
    base_url: str = ""
    api_key: str = ""


def _load() -> EmbedConfig:
    path = settings.embed_config_path
    if path.exists():
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            return EmbedConfig(
                d.get("backend", settings.embed_backend),
                d.get("model", settings.embed_model),
                d.get("base_url", settings.embed_base_url),
                d.get("api_key", settings.embed_api_key),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("讀取 embed_config.json 失敗，改用預設：%s", exc)
    return EmbedConfig(
        settings.embed_backend, settings.embed_model,
        settings.embed_base_url, settings.embed_api_key,
    )


_config: EmbedConfig = _load()
_local_cache: dict = {}          # model_name -> SentenceTransformer


def current() -> EmbedConfig:
    return _config


def configure(backend: str, model: str, base_url: str = "", api_key: str = "",
              persist: bool = True) -> EmbedConfig:
    global _config
    _config = EmbedConfig(backend, model, base_url.rstrip("/"), api_key)
    if persist:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.embed_config_path.write_text(
            json.dumps(asdict(_config), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("Embedding 設定已儲存：%s · %s", _config.backend, _config.model)
    return _config


def _local_model(name: str):
    if name not in _local_cache:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading local embedding model %s", name)
        _local_cache[name] = SentenceTransformer(name)
    return _local_cache[name]


def _embed_api(cfg: EmbedConfig, texts: list[str]) -> list[list[float]]:
    url = cfg.base_url.rstrip("/") + "/embeddings"
    headers = {"Authorization": f"Bearer {cfg.api_key or 'x'}"}
    r = httpx.post(url, json={"model": cfg.model, "input": texts},
                   headers=headers, timeout=180)
    r.raise_for_status()
    data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
    return [d["embedding"] for d in data]


def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def embed_texts(texts: list[str], normalize: bool = True) -> list[list[float]]:
    """向量化一批文字。本地模型內建正規化；API 結果在此正規化。"""
    if not texts:
        return []
    cfg = _config
    if cfg.backend == "api":
        vecs = _embed_api(cfg, texts)
        return [_normalize(v) for v in vecs] if normalize else vecs
    model = _local_model(cfg.model)
    return model.encode(
        texts, batch_size=BATCH_SIZE, normalize_embeddings=normalize,
        show_progress_bar=len(texts) > 20,
    ).tolist()


def probe_dim() -> int:
    """探測目前 embedding 的輸出維度。"""
    return len(embed_texts(["維度探測"], normalize=False)[0])


def warmup() -> None:
    if _config.backend == "local":
        _local_model(_config.model)


async def embed_pending(session: AsyncSession) -> int:
    """為所有尚未 embed 的 chunk 產生向量並寫回。"""
    result = await session.execute(select(Chunk).where(Chunk.embedding.is_(None)))  # type: ignore[union-attr]
    chunks = result.scalars().all()
    if not chunks:
        return 0
    vecs = embed_texts([c.text for c in chunks])
    for chunk, emb in zip(chunks, vecs):
        chunk.embedding = emb
    await session.commit()
    logger.info("Embedded %d chunks", len(chunks))
    return len(chunks)


async def reembed_all(session: AsyncSession) -> tuple[int, int]:
    """以目前 embedding 設定重新編碼全部 chunk；維度改變時自動調整欄位與索引。

    回傳 (重新編碼筆數, 維度)。
    """
    dim = probe_dim()
    await session.execute(sqltext("DROP INDEX IF EXISTS chunks_embedding_hnsw"))
    await session.execute(sqltext("UPDATE chunks SET embedding = NULL"))
    await session.execute(
        sqltext(f"ALTER TABLE chunks ALTER COLUMN embedding TYPE vector({dim})")
    )
    await session.commit()

    n = await embed_pending(session)

    try:
        await session.execute(sqltext(
            "CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw "
            "ON chunks USING hnsw (embedding vector_cosine_ops)"
        ))
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — HNSW 上限 2000 維，超過則略過索引
        logger.warning("HNSW 索引建立略過（維度 %d）：%s", dim, exc)
        await session.rollback()
    return n, dim
