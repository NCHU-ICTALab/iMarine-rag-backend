"""知識庫管理介面用的讀寫輔助函式（皆為綁定參數，無 SQL injection 風險）。"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def list_sources(session: AsyncSession) -> list[dict]:
    """列出所有來源與其 chunk 數量、啟用狀態。"""
    rows = await session.execute(text("""
        SELECT s.source_id, s.source_name, s.publisher, s.source_type,
               s.trust_score, s.phase, s.enabled,
               COUNT(c.chunk_id) AS chunk_count
        FROM sources s
        LEFT JOIN chunks c ON c.source_id = s.source_id
        GROUP BY s.source_id
        ORDER BY s.source_type, s.source_id
    """))
    return [dict(r) for r in rows.mappings()]


async def set_source_enabled(
    session: AsyncSession, source_id: str, enabled: bool
) -> None:
    """啟用/停用某來源；停用後檢索不再納入其 chunk。"""
    await session.execute(
        text("UPDATE sources SET enabled = :enabled WHERE source_id = :sid"),
        {"enabled": enabled, "sid": source_id},
    )
    await session.commit()


async def browse_chunks(
    session: AsyncSession,
    source_type: str | None = None,
    search: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """瀏覽知識庫 chunk，可依來源類型與關鍵字過濾。"""
    conditions = ["1=1"]
    params: dict = {"limit": limit}
    if source_type:
        conditions.append("source_type = :src_type")
        params["src_type"] = source_type
    if search:
        conditions.append("(title ILIKE :kw OR text ILIKE :kw)")
        params["kw"] = f"%{search}%"
    where = " AND ".join(conditions)
    rows = await session.execute(text(f"""
        SELECT chunk_id, source_type, title, section_path, original_url,
               published_at, credibility_score, token_count,
               embedding IS NOT NULL AS has_embedding,
               text
        FROM chunks
        WHERE {where}
        ORDER BY source_type, chunk_index
        LIMIT :limit
    """), params)
    return [dict(r) for r in rows.mappings()]
