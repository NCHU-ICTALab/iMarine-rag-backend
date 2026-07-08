from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://rag:rag@localhost:5432/rag_agent"
    data_dir: Path = Path("data")

    # LLM 供應商預設值（OpenAI 相容端點；可於介面覆寫並存檔）
    llm_provider: str = "Ollama（本地）"
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "gemma3:27b"

    # Embedding 預設值（本地 sentence-transformers 或 OpenAI 相容 API）
    embed_backend: str = "local"          # "local" | "api"
    embed_model: str = "google/EmbeddingGemma-300m"
    embed_base_url: str = "http://localhost:11434/v1"
    embed_api_key: str = "ollama"

    class Config:
        env_file = ".env"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def llm_config_path(self) -> Path:
        return self.data_dir / "llm_config.json"

    @property
    def embed_config_path(self) -> Path:
        return self.data_dir / "embed_config.json"


settings = Settings()
