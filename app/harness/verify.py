"""答案校验器。

核心规则：答案里出现的每一处引用 [制度编号 版本 条款]，
必须能在本次检索结果的元数据里找到完全匹配（编号+版本+条款号），
匹配不上就判定为幻觉/引用错误。这是"引用正确率"指标的计算依据。
"""
import re

# 引用格式：[X校发〔2025〕12号 v2 第五条]，条款号可省略
CITATION_PATTERN = re.compile(
    r"\[([^\]\s]+(?:〔\d{4}〕\d+号)?)\s+(v\d+)\s*(第[一二三四五六七八九十百零]+条)?\]"
)


def _parse_citations(answer: str) -> list[dict]:
    """从答案文本里抽出所有引用标记。"""
    cites = []
    for m in CITATION_PATTERN.finditer(answer):
        cites.append({
            "doc_no": m.group(1),
            "version": m.group(2),
            "clause": m.group(3) or "",
            "raw": m.group(0),
        })
    return cites


def verify_answer(answer: str, chunks: list[dict]) -> dict:
    """校验答案引用是否都命中检索结果。

    返回 {ok, reason, citations, bad_spans}
    - ok=True：所有引用都能对上元数据
    - bad_spans：对不上的引用列表，用于 badcase 分类
    """
    citations = _parse_citations(answer)

    # 检索结果里合法的 (doc_no, version, clause) 组合
    valid = set()
    valid_docs = set()
    for c in chunks:
        valid_docs.add((c.get("doc_no", ""), c.get("version", "")))
        if c.get("clause"):
            valid.add((c.get("doc_no", ""), c.get("version", ""), c.get("clause", "")))

    bad_spans = []
    for cite in citations:
        key = (cite["doc_no"], cite["version"], cite["clause"])
        doc_key = (cite["doc_no"], cite["version"])
        if cite["clause"] and key in valid:
            continue
        if not cite["clause"] and doc_key in valid_docs:
            continue
        # 条款号对不上但文号+版本对得上 → 引用错误；完全对不上 → 幻觉
        bad_spans.append({
            **cite,
            "type": "citation_error" if doc_key in valid_docs else "hallucination",
        })

    if not citations:
        # 没写任何引用也算不通过（除了"未找到依据"这种拒答）
        if "未找到" in answer or "没有找到" in answer:
            return {"ok": True, "reason": "refused", "citations": [], "bad_spans": []}
        return {"ok": False, "reason": "no_citation", "citations": [], "bad_spans": []}

    if bad_spans:
        reason = bad_spans[0]["type"]
        return {"ok": False, "reason": reason, "citations": citations, "bad_spans": bad_spans}

    return {"ok": True, "reason": "pass", "citations": citations, "bad_spans": []}


def check_hash_consistency(chunk: dict, mongo_version: dict) -> bool:
    """召回后强校验之二：chunk 内容的 sha256 必须和 MongoDB 版本链里的指纹一致。

    防止 Milvus 里的旧向量在制度修订后没被清干净，导致"答案引用的是废止内容"。
    """
    import hashlib
    digest = hashlib.sha256(chunk.get("text", "").encode("utf-8")).hexdigest()
    return digest == mongo_version.get("sha256", "")
