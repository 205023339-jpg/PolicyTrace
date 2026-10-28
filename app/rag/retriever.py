"""Milvus 向量召回 + 元数据过滤 + 召回后强校验。

三道防线：
1. 检索时 expr 过滤掉 status=revoked 的已废止版本（版本混乱问题的主要来源）
2. 按角色权限过滤（如考试违规处理细则的完整条款仅 teacher/admin 可见）
3. 召回后强校验：制度编号+版本+生效日期必须和 MongoDB 版本链一致，对不上直接丢弃
"""
import logging

from config.settings import get_settings
from app.rag.version_chain import VersionChain

logger = logging.getLogger(__name__)

# 权限表：文档可见的最低角色。student 能看大部分，敏感制度只给 teacher+
PERMISSION_MAP = {
    "ks-wg": {"student": "summary", "teacher": "full", "admin": "full"},   # 考试违规处理办法
    "wlaq": {"student": "summary", "teacher": "full", "admin": "full"},    # 校园网络安全管理规定
    "default": {"student": "full", "teacher": "full", "admin": "full"},
}


async def embed(texts: list[str]) -> list[list[float]]:
    """调 OpenAI 兼容接口拿 embedding（和 LLM 同一个网关）。"""
    from langchain_openai import OpenAIEmbeddings
    s = get_settings()
    emb = OpenAIEmbeddings(model="text-embedding-v3",
                           openai_api_key=s.llm_api_key,
                           openai_api_base=s.llm_base_url)
    return await emb.aembed_documents(texts)


def _get_milvus():
    from pymilvus import MilvusClient
    s = get_settings()
    return MilvusClient(uri=s.milvus_uri)


async def retrieve(queries: list[str], role: str, top_k: int | None = None) -> list[dict]:
    """多查询召回 + RRF 融合 + 权限过滤 + 元数据强校验。"""
    s = get_settings()
    top_k = top_k or s.retrieval_top_k
    client = _get_milvus()
    vectors = await embed(queries)

    # 每个查询各召回一批，只保留现行版本（status=effective）
    expr = 'status == "effective"'
    per_query_hits = []
    for vec in vectors:
        hits = client.search(
            collection_name=s.milvus_collection,
            data=[vec], filter=expr, limit=top_k + 4,
            output_fields=["doc_id", "doc_no", "version", "clause", "title",
                           "effective_date", "text", "sha256"],
        )
        per_query_hits.append(hits[0])

    fused = _rrf_fuse(per_query_hits, s.rrf_weight_vector, s.rrf_weight_keyword)

    # 权限过滤 + 强校验
    vc = VersionChain()
    results = []
    for hit in fused[:top_k]:
        doc_id = hit["entity"]["doc_id"]
        level = PERMISSION_MAP.get(doc_id, PERMISSION_MAP["default"]).get(role, "summary")
        if level == "summary":
            hit["entity"]["text"] = _summarize_for_student(hit["entity"]["text"])
        # 强校验：Milvus 里的元数据必须和 MongoDB 版本链完全一致
        if not _meta_check(vc, hit["entity"]):
            logger.warning("meta check failed, drop chunk: %s %s",
                           doc_id, hit["entity"].get("version"))
            continue
        results.append(hit["entity"])
    return results


def _rrf_fuse(hit_lists: list[list], w_vec: float, w_kw: float) -> list[dict]:
    """RRF（倒数排名融合）把多个查询的召回结果合成一个排序。

    简化版：第一个查询当"向量主召回"权重 w_vec，其余当扩展召回权重 w_kw。
    score = sum(w / (60 + rank))
    """
    scores: dict[int, float] = {}
    by_id: dict[int, dict] = {}
    for qi, hits in enumerate(hit_lists):
        w = w_vec if qi == 0 else w_kw
        for rank, hit in enumerate(hits):
            key = hash((hit["entity"]["doc_no"], hit["entity"]["version"],
                        hit["entity"].get("clause", "")))
            scores[key] = scores.get(key, 0) + w / (60 + rank)
            by_id[key] = hit
    ordered = sorted(scores, key=scores.get, reverse=True)
    return [by_id[k] for k in ordered]


def _meta_check(vc: VersionChain, entity: dict) -> bool:
    """召回后强校验：制度编号+版本+生效日期必须和 MongoDB 版本链匹配。"""
    chain = vc.get_chain(entity["doc_id"])
    if not chain or chain.get("doc_no") != entity.get("doc_no"):
        return False
    for v in chain.get("versions", []):
        if (v["version"] == entity.get("version")
                and v["effective_date"] == entity.get("effective_date")
                and v["status"] == "effective"):
            return True
    return False


def _summarize_for_student(text: str) -> str:
    """敏感制度对学生只返回摘要：截掉处理细则部分，保留结论性条款。"""
    marker = "【内部细则】"
    if marker in text:
        return text.split(marker)[0] + "（详细处理细则仅教职工可见，如需了解请联系辅导员）"
    return text
