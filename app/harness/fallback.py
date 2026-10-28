"""超时降级：Answer 节点 LLM 超时/挂掉时，不调 LLM，
直接把检索到的制度原文片段拼成回答返回，保证高并发下服务"有输出"。
"""

FALLBACK_HEADER = (
    "抱歉，智能回答服务暂时不可用，为您直接提供检索到的制度原文片段，"
    "请以原文为准：\n\n"
)


def build_fallback_answer(state: dict) -> tuple[str, list[dict]]:
    """从 state['chunks'] 拼降级回答。返回 (answer, citations)。"""
    chunks = state.get("chunks") or []
    if not chunks:
        return (
            "抱歉，系统暂时无法回答该问题，且未检索到相关制度原文。"
            "建议稍后重试，或到相关部门（教务处/学生工作处）现场咨询。",
            [],
        )

    lines = []
    citations = []
    seen = set()
    for c in chunks[:3]:  # 降级时只给最相关的 3 段，避免刷屏
        key = (c.get("doc_no"), c.get("version"), c.get("clause"))
        if key in seen:
            continue
        seen.add(key)
        title = f"《{c.get('title', '相关制度')}》{c.get('clause', '')}".strip()
        lines.append(f"—— {title}（{c.get('doc_no', '')} {c.get('version', '')}）——\n{c.get('text', '')}\n")
        citations.append({
            "doc_no": c.get("doc_no", ""),
            "version": c.get("version", ""),
            "clause": c.get("clause", ""),
            "effective_date": c.get("effective_date", ""),
        })

    return FALLBACK_HEADER + "\n".join(lines), citations
