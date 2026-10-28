"""LangGraph 编排：Intent → Rewrite → Retrieval → Answer → Verify 的 DAG。

Verify 不通过时会回到 Retrieval 重试（最多 max_verify_retry 次），
重试还不行就走 fallback 降级分支。每个节点用 asyncio.wait_for 控超时，
LLM 相关节点套 tenacity 重试。
"""
import asyncio
import logging
from typing import TypedDict

from langgraph.graph import END, StateGraph
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import get_settings
from app.harness import nodes
from app.harness.fallback import build_fallback_answer
from app.feedback.trace_store import save_trace

logger = logging.getLogger(__name__)


class WorkflowState(TypedDict, total=False):
    trace_id: str
    session_id: str
    question: str
    role: str
    # 节点产出
    intent: dict            # {type, entities}
    rewritten_queries: list # 改写 + 扩展后的查询列表
    chunks: list            # 检索到的条款片段（带元数据）
    answer: str
    citations: list
    verify_result: dict     # {ok, reason, bad_spans}
    verify_retry: int
    verify_status: str      # pass / retry_pass / fallback
    degraded: bool
    trace_steps: list       # 每个节点的输入输出，落盘用于回放
    error: str


# 带超时的节点执行器：超时/异常都记进 trace_steps，不直接抛出去把服务打挂
async def _run_node(state: WorkflowState, name: str, coro_fn, timeout: int):
    s = get_settings()
    step = {"node": name, "input_keys": sorted(k for k in state if k != "trace_steps")}
    try:
        await asyncio.wait_for(coro_fn(state), timeout=timeout)
        step["status"] = "ok"
    except asyncio.TimeoutError:
        step["status"] = "timeout"
        state["error"] = f"{name} timeout after {timeout}s"
        logger.warning("node %s timeout", name)
    except Exception as e:  # noqa: BLE001 学生项目，先兜住所有异常保证可用性
        step["status"] = "error"
        state["error"] = f"{name}: {e}"
        logger.exception("node %s failed", name)
    step["output_keys"] = sorted(k for k in state if k != "trace_steps")
    state.setdefault("trace_steps", []).append(step)
    return state


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=4), reraise=True)
async def _answer_with_retry(state: WorkflowState):
    # Answer 节点调 LLM，网络抖动比较常见，单独套一层 tenacity
    await nodes.answer_node(state)


async def intent_node(state):
    return await _run_node(state, "intent", nodes.intent_node, get_settings().node_timeout_intent)


async def rewrite_node(state):
    return await _run_node(state, "rewrite", nodes.rewrite_node, get_settings().node_timeout_rewrite)


async def retrieval_node(state):
    return await _run_node(state, "retrieval", nodes.retrieval_node, get_settings().node_timeout_retrieval)


async def answer_node(state):
    return await _run_node(state, "answer", _answer_with_retry, get_settings().node_timeout_answer)


async def verify_node(state):
    return await _run_node(state, "verify", nodes.verify_node, get_settings().node_timeout_verify)


async def fallback_node(state):
    # 降级：不用 LLM，直接把检索到的原文片段拼出来返回
    state["answer"], state["citations"] = build_fallback_answer(state)
    state["degraded"] = True
    state["verify_status"] = "fallback"
    state.setdefault("trace_steps", []).append({"node": "fallback", "status": "ok"})
    return state


def route_after_verify(state: WorkflowState) -> str:
    """Verify 之后的分支：通过→结束；不通过且还有重试次数→重新检索；否则降级。"""
    vr = state.get("verify_result") or {}
    if vr.get("ok"):
        return END
    if state.get("verify_retry", 0) < get_settings().max_verify_retry:
        return "retrieval"
    return "fallback"


def route_after_intent(state: WorkflowState) -> str:
    # 意图识别挂了（超时/异常）也别直接失败，继续往下走，用原问题检索
    return "rewrite"


def build_graph():
    g = StateGraph(WorkflowState)
    g.add_node("intent", intent_node)
    g.add_node("rewrite", rewrite_node)
    g.add_node("retrieval", retrieval_node)
    g.add_node("answer", answer_node)
    g.add_node("verify", verify_node)
    g.add_node("fallback", fallback_node)

    g.set_entry_point("intent")
    g.add_edge("intent", "rewrite")
    g.add_edge("rewrite", "retrieval")
    g.add_edge("retrieval", "answer")
    g.add_edge("answer", "verify")
    g.add_conditional_edges("verify", route_after_verify,
                            {END: END, "retrieval": "retrieval", "fallback": "fallback"})
    g.add_edge("fallback", END)
    return g.compile()


_graph = None


async def run_workflow(trace_id: str, session_id: str, question: str, role: str) -> dict:
    global _graph
    if _graph is None:
        _graph = build_graph()

    state: WorkflowState = {
        "trace_id": trace_id, "session_id": session_id,
        "question": question, "role": role,
        "verify_retry": 0, "degraded": False, "trace_steps": [],
    }
    final = await _graph.ainvoke(state)

    result = {
        "answer": final.get("answer", "系统繁忙，请稍后再试。"),
        "citations": final.get("citations", []),
        "degraded": final.get("degraded", False),
        "verify_status": final.get("verify_status", "pass"),
    }
    # trace 落盘，供 GET /api/trace/{id} 和 badcase 分析回放
    save_trace(trace_id, session_id, question, role, final.get("trace_steps", []), result)
    return result
