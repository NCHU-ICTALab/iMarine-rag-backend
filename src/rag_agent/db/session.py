from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from ..config import settings
from . import models as _models  # noqa: F401 — registers tables with SQLModel.metadata
from ..audit import models as _audit_models  # noqa: F401 — registers audit.* tables

# NullPool：不使用連線池，每次請求建立新連線並在結束時關閉。
# 這樣連線不綁定到特定 event loop，跨 loop（pytest、Streamlit uvloop）皆可安全使用。
engine = create_async_engine(settings.database_url, echo=False, poolclass=NullPool)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def create_tables() -> None:
    async with engine.begin() as conn:
        # 稽核紀錄放獨立 audit schema，須先建立 schema 再 create_all
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS audit"))
        await conn.run_sync(SQLModel.metadata.create_all)
        # pgvector HNSW 索引：加速 dense 檢索的最近鄰查詢（cosine）。
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw "
            "ON chunks USING hnsw (embedding vector_cosine_ops)"
        ))
