"""知識庫管理端點：建立/刪除知識庫、上傳文件 → ingest、列出/刪除文件。

供設定頁「知識庫管理」的管理者維護自建知識庫（source_type="uploaded"）。
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.session import create_tables
from ...ingestion.upload import (
    create_kb,
    delete_document,
    delete_kb,
    ingest_file,
    list_documents,
)
from ..deps import get_session

router = APIRouter(prefix="/kb", tags=["kb"])


class CreateKbIn(BaseModel):
    name: str


@router.post("")
async def make_kb(body: CreateKbIn, session: AsyncSession = Depends(get_session)) -> dict:
    """建立一個新的（空的）使用者知識庫。"""
    await create_tables()
    src = await create_kb(session, body.name)
    return {"source_id": src.source_id, "source_name": src.source_name}


@router.delete("/{source_id}")
async def remove_kb(source_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """刪除知識庫及其所有文件與 chunk。"""
    await delete_kb(session, source_id)
    return {"ok": True, "source_id": source_id}


@router.get("/{source_id}/documents")
async def get_documents(
    source_id: str, session: AsyncSession = Depends(get_session)
) -> list[dict]:
    """列出某知識庫的文件清單（含 chunk 數）。"""
    return await list_documents(session, source_id)


@router.post("/{source_id}/documents")
async def upload_document(
    source_id: str,
    file: UploadFile = File(...),
    chunk_size: int = Form(512),
    chunk_overlap: int = Form(64),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """上傳一份文件到知識庫：解析 → 切段 → embedding。支援 TXT / MD / PDF / DOCX。"""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="檔案為空")
    try:
        return await ingest_file(
            session, source_id, file.filename or "upload",
            raw, chunk_size=chunk_size, chunk_overlap=chunk_overlap,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{source_id}/documents/{doc_id}")
async def remove_document(
    source_id: str, doc_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    """刪除單一文件及其 chunk。"""
    await delete_document(session, doc_id)
    return {"ok": True, "doc_id": doc_id}
