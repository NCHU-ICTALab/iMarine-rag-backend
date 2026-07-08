"""設定端點：供前端「系統設定 · 政策報告」對接真後端。

生成模型（llm_config）與 Embedding（embed_config）的讀取/設定/測試連線/列模型。
api_key 只回傳末四碼遮罩，不回明文。
"""

from fastapi import APIRouter
from pydantic import BaseModel

from ...generation import provider
from ...indexing import embedding

router = APIRouter(prefix="/settings", tags=["settings"])


def _tail(key: str) -> str:
    return f"…{key[-4:]}" if key and len(key) >= 4 else ("已設定" if key else "")


class LLMConfigIn(BaseModel):
    provider: str = "自訂 / 其他 OpenAI 相容"
    base_url: str
    api_key: str = ""
    model: str


class EmbedConfigIn(BaseModel):
    backend: str            # "local" | "api"
    model: str
    base_url: str = ""
    api_key: str = ""


class TestIn(BaseModel):
    base_url: str
    api_key: str = ""
    model: str


@router.get("")
async def get_settings() -> dict:
    """目前的生成模型與 Embedding 設定（api_key 遮罩）。"""
    llm = provider.current()
    emb = embedding.current()
    return {
        "llm": {
            "provider": llm.provider, "base_url": llm.base_url,
            "model": llm.model, "api_key_tail": _tail(llm.api_key),
        },
        "embed": {
            "backend": emb.backend, "model": emb.model,
            "base_url": emb.base_url, "api_key_tail": _tail(emb.api_key),
        },
        "presets": provider.PRESETS,
    }


@router.post("/llm")
async def set_llm(cfg: LLMConfigIn) -> dict:
    """設定生成模型（落檔 llm_config.json，即時生效）。"""
    c = provider.configure(cfg.provider, cfg.base_url, cfg.api_key, cfg.model)
    return {"ok": True, "provider": c.provider, "base_url": c.base_url, "model": c.model}


@router.post("/embed")
async def set_embed(cfg: EmbedConfigIn) -> dict:
    """設定 Embedding 後端（落檔 embed_config.json）。更換模型後維度可能改變，需重新索引。"""
    c = embedding.configure(cfg.backend, cfg.model, cfg.base_url, cfg.api_key)
    return {"ok": True, "backend": c.backend, "model": c.model}


@router.post("/test")
async def test(cfg: TestIn) -> dict:
    """測試「指定」的生成模型設定（不改動目前 config）。"""
    ok, message = provider.probe(cfg.base_url, cfg.api_key, cfg.model)
    return {"ok": ok, "message": message}


@router.get("/models")
async def models(base_url: str) -> dict:
    """列出指定 base_url（Ollama）已安裝的模型，供設定頁下拉。"""
    return {"models": provider.list_ollama_models(base_url)}
