"""DAG 各节点的具体实现。节点函数直接原地改 state（LangGraph 里 state 是共享 dict）。"""
import json
import logging

from langchain_openai import ChatOpenAI

from config.settings import get_settings
from app.memory.context_builder import ContextBuilder
from app.memory.working import WorkingMemory
from app.memory.episodic import EpisodicMemory
from app.memory.semantic import SemanticMemory
from app.rag.retriever import retrieve
from app.harness.verify import verify_answer
from app.feedback.query_expansion import expand_query

logger = logging.getLogger(__name__)


def get_llm(temperature: float = 0.2) -> ChatOpenAI:
    s = get_settings()
    return ChatOpenAI(
        model=s.llm_model,
        api_key=s.llm_api_key,
        base_url=s.llm_base_url,
        temperature=temperature,
    )


async def intent_node(state: dict):
    """意图识别：判断问题类型（政策咨询/办事流程/申诉投诉/闲聊），抽实体（制度名、文号）。"""
    prompt = (
        "判断下面这个校内制度咨询问题的意图，输出 JSON："
        '{"type": "policy_qa|process|complaint|chitchat", '
        '"entities": {"policy_name": "", "doc_no": ""}}\n'
        f"问题：{state['question']}"
    )
    resp = await get_llm(temperature=0).ainvoke(prompt)
    try:
        state["intent"] = json.loads(resp.content)
    except json.JSONDecodeError:
        # LLM 输出不规范时兜底
        state["intent"] = {"type": "policy_qa", "entities": {}}


async def rewrite_node(state: dict):
    """查询改写 + 扩展：补全指代（结合工作记忆里的槽位），再生成多个查询变体。"""
    s = get_settings()
    wm = WorkingMemory(state["session_id"])
    slots = wm.get_slots()

    # 先让 LLM 把口语化问题改写成适合检索的规范问法
    prompt = (
        "把学生的口语问题改写成适合检索校内制度库的规范问法，只输出改写结果。\n"
        f"已知上下文槽位：{json.dumps(slots, ensure_ascii=False)}\n"
        f"问题：{state['question']}"
    )
    resp = await get_llm().ainvoke(prompt)
    rewritten = resp.content.strip()

    # 再走查询扩展（jieba 同义词 + LLM 变体），提高召回命中率
    queries = await expand_query(rewritten)
    state["rewritten_queries"] = queries[:4]  # 最多 4 个查询，控制检索耗时
    wm.set_slot("last_query", rewritten)


async def retrieval_node(state: dict):
    """检索：多查询召回 + RRF 融合 + 元数据强校验 + 权限过滤。"""
    s = get_settings()
    queries = state.get("rewritten_queries") or [state["question"]]
    # 重试时收紧 top_k，让结果更聚焦（迭代日志 v0.3 的改动）
    top_k = s.retrieval_top_k if state.get("verify_retry", 0) == 0 else max(3, s.retrieval_top_k - 2)

    chunks = await retrieve(queries, role=state["role"], top_k=top_k)

    # 语义记忆命中：以前沉淀过的标准问答对，直接作为高置信参考
    sem = SemanticMemory().lookup(state["question"])
    if sem:
        chunks.insert(0, {
            "text": f"[历史标准答案参考] {sem['answer']}",
            "doc_no": sem["doc_no"], "version": sem["version"],
            "clause": sem.get("clause", ""), "source": "semantic_memory",
        })
    state["chunks"] = chunks


async def answer_node(state: dict):
    """生成回答：ContextBuilder 按权限/时效/token 预算组装上下文，再让 LLM 作答。"""
    s = get_settings()
    ctx = ContextBuilder(role=state["role"]).build(
        question=state["question"],
        chunks=state.get("chunks", []),
        session_id=state["session_id"],
    )
    prompt = (
        "你是校内制度问答助手 PolicyTrace。只根据【制度原文】回答，"
        "每个结论后面必须用 [制度编号 版本 条款号] 标注引用，"
        "原文没有的信息一律回答\"该问题在现有制度中未找到依据\"，禁止编造。\n\n"
        f"【制度原文】\n{ctx}\n\n【问题】{state['question']}"
    )
    resp = await get_llm(temperature=0.1).ainvoke(prompt)
    state["answer"] = resp.content.strip()

    # 从答案里抽引用标记，作为 citations 返回给前端
    state["citations"] = verify_answer(state["answer"], state.get("chunks", []))["citations"]

    # 写工作记忆 + 超过阈值触发情景摘要
    wm = WorkingMemory(state["session_id"])
    wm.append_turn(state["question"], state["answer"])
    EpisodicMemory(state["session_id"]).maybe_summarize(wm)


async def verify_node(state: dict):
    """校验节点：引用必须命中检索结果元数据，否则标记幻觉并重试。"""
    result = verify_answer(state.get("answer", ""), state.get("chunks", []))
    state["verify_result"] = result
    if result["ok"]:
        state["verify_status"] = (
            "pass" if state.get("verify_retry", 0) == 0 else "retry_pass"
        )
    else:
        state["verify_retry"] = state.get("verify_retry", 0) + 1
        # 把校验失败原因写回 state，重检索时可以参考
        logger.info("verify failed (%s), retry=%d", result["reason"], state["verify_retry"])
