"""Badcase 自动分类器：规则优先，规则分不出来的再用 LLM 判断。

四类：
- retrieval_miss     检索未命中（召回为空或相关度低）
- citation_error     引用错误（文号/版本对但条款号对不上）
- hallucination      幻觉（引用了检索结果里根本不存在的制度）
- permission_denied  权限问题（越权提问被拦截，或该看的没看到）
"""
import json
import logging

from app.db.mongo import get_mongo_db

logger = logging.getLogger(__name__)

CATEGORIES = ["retrieval_miss", "citation_error", "hallucination", "permission_denied"]


def classify_by_rules(trace: dict) -> str | None:
    """先用规则分类，分不出来返回 None 交给 LLM。"""
    steps = {s["node"]: s for s in trace.get("trace_steps", [])}
    answer = trace.get("result", {}).get("answer", "")
    chunks = trace.get("chunks", [])

    # 检索节点超时 / 召回为空 → 检索未命中
    if steps.get("retrieval", {}).get("status") != "ok" or not chunks:
        return "retrieval_miss"

    # 降级回答基本都源于检索或 LLM 问题
    if trace.get("result", {}).get("degraded"):
        return "retrieval_miss"

    verify = trace.get("verify_result") or {}
    if verify.get("bad_spans"):
        span_type = verify["bad_spans"][0].get("type")
        if span_type == "hallucination":
            return "hallucination"
        if span_type == "citation_error":
            return "citation_error"

    # 答案里出现权限提示语 → 权限问题
    if "仅教职工可见" in answer or "权限" in answer and "不足" in answer:
        return "permission_denied"

    return None


async def classify_by_llm(trace: dict) -> str:
    """规则分不出来的，把 trace 摘要喂给 LLM 判断。"""
    from app.harness.nodes import get_llm
    digest = {
        "question": trace.get("question"),
        "role": trace.get("role"),
        "answer": (trace.get("result", {}).get("answer") or "")[:500],
        "retrieved_docs": [
            {"doc_no": c.get("doc_no"), "clause": c.get("clause")}
            for c in trace.get("chunks", [])[:6]
        ],
    }
    prompt = (
        "分析这条校内制度问答记录的问题类型，只能输出以下之一："
        f"{CATEGORIES}，输出 JSON {{\"category\": \"...\"}}。\n"
        f"记录：{json.dumps(digest, ensure_ascii=False)}"
    )
    try:
        resp = await get_llm(temperature=0).ainvoke(prompt)
        cat = json.loads(resp.content).get("category", "retrieval_miss")
        return cat if cat in CATEGORIES else "retrieval_miss"
    except Exception:  # noqa: BLE001
        logger.warning("llm classify failed for trace %s", trace.get("trace_id"))
        return "retrieval_miss"


async def classify_badcase(trace: dict) -> dict:
    """入口：返回 {trace_id, category, by}。结果写进 MongoDB badcases 集合。"""
    category = classify_by_rules(trace)
    by = "rule"
    if category is None:
        category = await classify_by_llm(trace)
        by = "llm"
    record = {
        "trace_id": trace.get("trace_id"),
        "question": trace.get("question"),
        "category": category,
        "classified_by": by,
    }
    get_mongo_db()["badcases"].insert_one(record)
    return record
