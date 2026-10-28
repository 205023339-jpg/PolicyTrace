"""Trace 落盘 MongoDB + 回放。

每次问答把每个节点的输入输出、耗时、状态都记下来，
GET /api/trace/{id} 和 scripts/replay_trace.py 都从这里读。
"""
import logging
from datetime import datetime

from app.db.mongo import trace_col

logger = logging.getLogger(__name__)


def save_trace(trace_id: str, session_id: str, question: str,
               role: str, steps: list[dict], result: dict):
    """落盘一条 trace。steps 是 DAG 执行过程中每个节点记的 {node, status, ...}。"""
    doc = {
        "trace_id": trace_id,
        "session_id": session_id,
        "question": question,
        "role": role,
        "steps": steps,
        "result": result,
        "created_at": datetime.now().isoformat(),
    }
    try:
        trace_col().insert_one(doc)
    except Exception:  # noqa: BLE001 trace 落盘失败不能影响回答返回
        logger.warning("save trace failed: %s", trace_id)


def load_trace(trace_id: str) -> dict | None:
    doc = trace_col().find_one({"trace_id": trace_id}, {"_id": 0})
    return doc


def list_recent_traces(limit: int = 20) -> list[dict]:
    """最近的 trace 列表，badcase 分析时用。"""
    cursor = trace_col().find({}, {"_id": 0, "steps": 0}) \
        .sort("created_at", -1).limit(limit)
    return list(cursor)
