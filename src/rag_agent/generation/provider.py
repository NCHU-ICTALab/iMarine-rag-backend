"""LLM 供應商抽象：統一走 OpenAI 相容的 /chat/completions 端點。

Ollama、vLLM、OpenAI、OpenRouter、Groq、Together、LM Studio… 都提供相容端點，
因此只要一份實作即可切換：使用者填 base_url / api_key / model 即可。
設定存於 data_dir/llm_config.json，程序內以 module 全域保存（跨 Streamlit rerun）。
"""

import json
import logging
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

# 常見供應商的預設 base_url（供介面帶入）
PRESETS: dict[str, str] = {
    "Ollama（本地）": "http://localhost:11434/v1",
    "OpenAI": "https://api.openai.com/v1",
    "OpenRouter": "https://openrouter.ai/api/v1",
    "Groq": "https://api.groq.com/openai/v1",
    "Together": "https://api.together.xyz/v1",
    "自訂 / 其他 OpenAI 相容": "",
}


@dataclass
class LLMConfig:
    provider: str
    base_url: str
    api_key: str
    model: str


def _load() -> LLMConfig:
    path = settings.llm_config_path
    if path.exists():
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            return LLMConfig(
                d.get("provider", settings.llm_provider),
                d.get("base_url", settings.llm_base_url),
                d.get("api_key", settings.llm_api_key),
                d.get("model", settings.llm_model),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("讀取 llm_config.json 失敗，改用預設：%s", exc)
    return LLMConfig(
        settings.llm_provider, settings.llm_base_url,
        settings.llm_api_key, settings.llm_model,
    )


_config: LLMConfig = _load()


def current() -> LLMConfig:
    return _config


def configure(provider: str, base_url: str, api_key: str, model: str,
              persist: bool = True) -> LLMConfig:
    global _config
    _config = LLMConfig(provider, base_url.rstrip("/"), api_key, model)
    if persist:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.llm_config_path.write_text(
            json.dumps(asdict(_config), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("LLM 設定已儲存：%s · %s", _config.provider, _config.model)
    return _config


# ── 推論 ─────────────────────────────────────────────────────────────────

def _url(cfg: LLMConfig) -> str:
    return cfg.base_url.rstrip("/") + "/chat/completions"


def _headers(cfg: LLMConfig) -> dict:
    return {
        "Authorization": f"Bearer {cfg.api_key or 'x'}",
        "Content-Type": "application/json",
    }


def _messages(prompt: str, system: str | None) -> list[dict]:
    msgs = [{"role": "system", "content": system}] if system else []
    msgs.append({"role": "user", "content": prompt})
    return msgs


def complete(prompt: str, max_tokens: int = 512, temperature: float = 0.0,
             system: str | None = None) -> str:
    cfg = _config
    payload = {
        "model": cfg.model,
        "messages": _messages(prompt, system),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    r = httpx.post(_url(cfg), json=payload, headers=_headers(cfg), timeout=300)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def stream(prompt: str, max_tokens: int = 512, temperature: float = 0.0,
           system: str | None = None) -> Iterator[str]:
    cfg = _config
    payload = {
        "model": cfg.model,
        "messages": _messages(prompt, system),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    with httpx.stream("POST", _url(cfg), json=payload,
                      headers=_headers(cfg), timeout=300) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            if line.startswith("data: "):
                line = line[6:]
            if line.strip() == "[DONE]":
                break
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            delta = obj.get("choices", [{}])[0].get("delta", {}).get("content")
            if delta:
                yield delta


def test_connection() -> tuple[bool, str]:
    """對目前設定做一次極短呼叫，回傳 (是否成功, 訊息)。"""
    try:
        t = time.perf_counter()
        out = complete("請只回覆「OK」兩個字。", max_tokens=16)
        return True, f"連線成功（{time.perf_counter() - t:.1f}s）· 回應：{out.strip()[:40]}"
    except Exception as exc:  # noqa: BLE001
        return False, f"連線失敗：{exc}"


def probe(base_url: str, api_key: str, model: str) -> tuple[bool, str]:
    """測試「指定」的設定（不改動當前 config，不落檔），供設定頁的測試連線用。"""
    cfg = LLMConfig(provider="probe", base_url=base_url.rstrip("/"), api_key=api_key, model=model)
    try:
        t = time.perf_counter()
        payload = {
            "model": cfg.model,
            "messages": _messages("請只回覆「OK」兩個字。", None),
            "max_tokens": 16,
            "stream": False,
        }
        r = httpx.post(_url(cfg), json=payload, headers=_headers(cfg), timeout=30)
        r.raise_for_status()
        out = r.json()["choices"][0]["message"]["content"]
        return True, f"連線成功（{time.perf_counter() - t:.1f}s）· 回應：{out.strip()[:40]}"
    except Exception as exc:  # noqa: BLE001
        return False, f"連線失敗：{exc}"


def list_ollama_models(base_url: str) -> list[str]:
    """若指向 Ollama，列出本機已安裝的模型（供介面下拉選單）。"""
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3].rstrip("/")
    try:
        r = httpx.get(root + "/api/tags", timeout=5)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:  # noqa: BLE001
        return []
