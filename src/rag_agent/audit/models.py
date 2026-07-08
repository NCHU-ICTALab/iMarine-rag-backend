"""稽核紀錄資料表，置於獨立的 `audit` schema（spec 6.5）。

與 public schema 的業務資料表分離；JSONL 僅作每日封存匯出，DB 為主要真相來源。
"""

from datetime import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class GenerationRun(SQLModel, table=True):
    __tablename__ = "generation_runs"
    __table_args__ = {"schema": "audit"}

    id: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=datetime.utcnow, index=True)
    query: str
    task_type: str                       # chat_qa | report_generation
    evidence_package_id: str
    report_id: str | None = None
    confidence: float = 0.0
    conflict_flag: str = "not_evaluated"
    evidence_count: int = 0
    cited_ids: list = Field(default_factory=list, sa_column=Column(JSON))
    citation_coverage: float = 0.0
    answer_length: int = 0
