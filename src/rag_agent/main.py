import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import (
    audit, chat, evidence, generate, ingest, kb, policy, report, retrieve, schedule,
    settings, sources,
)
from .db.session import create_tables
from . import scheduler

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="iMarine RAG Agent",
    description="政策輔助報告管線 — Evidence-grounded LLM answers with audit trail",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    await create_tables()
    scheduler.start()          # 每日排程背景迴圈


app.include_router(ingest.router, prefix="/api")
app.include_router(retrieve.router, prefix="/api")
app.include_router(evidence.router, prefix="/api")
app.include_router(generate.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(report.router, prefix="/api")
app.include_router(policy.router, prefix="/api")
app.include_router(schedule.router, prefix="/api")
app.include_router(sources.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(kb.router, prefix="/api")
app.include_router(audit.router, prefix="/api")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": app.version}
