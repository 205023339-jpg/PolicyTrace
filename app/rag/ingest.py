"""制度文档入库：解析 markdown → 按条款切分 → 算指纹 → 写 MongoDB 版本链 + Milvus 向量。

markdown 格式约定（见 data/policies/）：
---
doc_id: jxj-2025-12
title: 学生奖学金评定办法
doc_no: X校发〔2025〕12号
effective_date: 2025-09-01
dept: 学生工作处
version: v2
diff_summary: 提高一等奖学金比例，新增创新创业加分项
min_role: student
---
正文（第一条 ... 第二条 ...）
"""
import hashlib
import logging
import re

from config.settings import get_settings
from app.rag.version_chain import VersionChain

logger = logging.getLogger(__name__)

CLAUSE_SPLIT = re.compile(r"(?=第[一二三四五六七八九十百零]+条)")


def parse_policy_md(path: str) -> tuple[dict, str]:
    """解析 frontmatter + 正文。"""
    raw = open(path, encoding="utf-8").read()
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
    if not m:
        raise ValueError(f"{path} 缺少 frontmatter")
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, m.group(2).strip()


def split_clauses(body: str) -> list[dict]:
    """按"第X条"切分成条款级 chunk。"""
    chunks = []
    parts = [p for p in CLAUSE_SPLIT.split(body) if p.strip()]
    for part in parts:
        first_line = part.strip().splitlines()[0]
        m = re.match(r"(第[一二三四五六七八九十百零]+条)", first_line)
        clause_no = m.group(1) if m else ""
        chunks.append({"clause": clause_no, "text": part.strip()})
    return chunks


async def ingest_file(path: str) -> int:
    """入库单个制度文件，返回写入 Milvus 的 chunk 数。"""
    s = get_settings()
    meta, body = parse_policy_md(path)

    # 1. 写 MongoDB 版本链（同 doc_id 会自动把旧版标记废止）
    vc = VersionChain()
    version_rec = vc.add_version(
        doc_id=meta["doc_id"], title=meta["title"], doc_no=meta["doc_no"],
        content=body, effective_date=meta["effective_date"],
        diff_summary=meta.get("diff_summary", ""),
    )

    # 2. 条款切分 + 逐条算指纹，写 Milvus
    from pymilvus import MilvusClient
    from app.rag.retriever import embed

    client = MilvusClient(uri=s.milvus_uri)
    if not client.has_collection(s.milvus_collection):
        _create_collection(client)

    chunks = split_clauses(body)
    vectors = await embed([c["text"] for c in chunks])
    rows = []
    for c, vec in zip(chunks, vectors):
        rows.append({
            "id": hashlib.md5(
                f"{meta['doc_id']}:{version_rec['version']}:{c['clause']}:{c['text'][:20]}"
                .encode("utf-8")).hexdigest(),
            "vector": vec,
            "doc_id": meta["doc_id"],
            "doc_no": meta["doc_no"],
            "version": version_rec["version"],
            "clause": c["clause"],
            "title": meta["title"],
            "effective_date": meta["effective_date"],
            "dept": meta.get("dept", ""),
            "status": "effective",
            "sha256": hashlib.sha256(c["text"].encode("utf-8")).hexdigest(),
            "text": c["text"],
        })
    client.insert(collection_name=s.milvus_collection, data=rows)
    logger.info("ingested %s %s: %d chunks", meta["doc_no"],
                version_rec["version"], len(rows))
    return len(rows)


def _create_collection(client):
    """建集合：向量 + 标量元数据字段，元数据字段建索引用于 filter。"""
    s = get_settings()
    from pymilvus import DataType
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=s.embedding_dim)
    for f in ["doc_id", "doc_no", "version", "clause", "title",
              "effective_date", "dept", "status", "sha256", "text"]:
        schema.add_field(f, DataType.VARCHAR, max_length=4096)

    index_params = client.prepare_index_params()
    index_params.add_index(field_name="vector", index_type="FLAT", metric_type="COSINE")
    index_params.add_index(field_name="status", index_type="INVERTED")
    client.create_collection(s.milvus_collection, schema=schema,
                             index_params=index_params)


async def revoke_old_vectors(doc_id: str, keep_version: str):
    """制度改版后，把 Milvus 里旧版本的向量标记成 revoked（filter 时就查不到了）。"""
    s = get_settings()
    from pymilvus import MilvusClient
    client = MilvusClient(uri=s.milvus_uri)
    rows = client.query(
        collection_name=s.milvus_collection,
        filter=f'doc_id == "{doc_id}" and version != "{keep_version}"',
        output_fields=["id"],
    )
    if rows:
        client.delete(collection_name=s.milvus_collection,
                      ids=[r["id"] for r in rows])
        logger.info("revoked %d old chunks of %s", len(rows), doc_id)
