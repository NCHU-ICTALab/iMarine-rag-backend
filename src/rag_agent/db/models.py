from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column
from sqlmodel import Field, SQLModel


class Source(SQLModel, table=True):
    __tablename__ = "sources"

    source_id: str = Field(primary_key=True)
    source_name: str
    publisher: str
    source_type: str          # regulation | news
    jurisdiction: str
    license_type: str
    access_method: str
    feed_url: str | None = None
    update_frequency: str
    trust_score: int
    attribution_required: bool = True
    full_text_indexing: bool = True
    phase: str = "MVP"
    enabled: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RawDocument(SQLModel, table=True):
    __tablename__ = "raw_documents"

    id: int | None = Field(default=None, primary_key=True)
    source_id: str = Field(foreign_key="sources.source_id", index=True)
    source_type: str
    source_url: str
    raw_format: str
    content_pointer: str | None = None   # local path（MVP）
    fetched_at: datetime
    source_version: str | None = None
    checksum: str = Field(unique=True, index=True)


class Chunk(SQLModel, table=True):
    __tablename__ = "chunks"

    chunk_id: str = Field(primary_key=True)
    raw_document_id: int = Field(foreign_key="raw_documents.id", index=True)
    source_id: str = Field(foreign_key="sources.source_id", index=True)
    source_type: str                        # regulation | news
    title: str
    text: str
    section_path: str | None = None         # 第12條 | para-0 …
    original_url: str = ""
    published_at: datetime | None = None
    credibility_score: int = 0
    chunk_index: int = 0
    token_count: int = 0
    checksum: str = Field(index=True)
    embedding: list[float] | None = Field(default=None, sa_column=Column(Vector(768)))
    created_at: datetime = Field(default_factory=datetime.utcnow)
