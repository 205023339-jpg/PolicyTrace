"""FastAPI 入口。

两个核心接口：
- POST /api/ask        问答入口，role 决定权限（student/teacher/admin）
- GET  /api/trace/{id} 查一次问答的完整执行轨迹（每个节点输入输出）
"""
import uuid
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.harness.dag import run_workflow
from app.feedback.trace_store import load_trace

app = FastAPI(title="PolicyTrace", description="校内制度溯源与策略迭代助手")


class AskRequest(BaseModel):
    question: str = Field(..., min_length=2, max_length=500)
    role: Literal["student", "teacher", "admin"] = "student"
    session_id: Optional[str] = None  # 不传就新开会话


class AskResponse(BaseModel):
    trace_id: str
    answer: str
    citations: list[dict] = []        # [{doc_no, version, clause, effective_date}]
    degraded: bool = False            # 是否走了超时降级
    verify_status: str = "pass"       # pass / retry_pass / fallback


@app.post("/api/ask", response_model=AskResponse)
async def ask(req: AskRequest):
    session_id = req.session_id or uuid.uuid4().hex[:12]
    trace_id = uuid.uuid4().hex
    result = await run_workflow(
        trace_id=trace_id,
        session_id=session_id,
        question=req.question,
        role=req.role,
    )
    return AskResponse(
        trace_id=trace_id,
        answer=result["answer"],
        citations=result.get("citations", []),
        degraded=result.get("degraded", False),
        verify_status=result.get("verify_status", "pass"),
    )


@app.get("/api/trace/{trace_id}")
async def get_trace(trace_id: str):
    trace = load_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return trace


@app.get("/api/health")
async def health():
    return {"status": "ok"}
