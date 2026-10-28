"""把 data/policies 下的制度 markdown 灌入 MongoDB（版本链）+ Milvus（向量）。

用法：
    python scripts/init_data.py

注意：同一 doc_id 的多个版本要按生效日期先后灌入（脚本内已按 frontmatter
的 effective_date 排序），先 v1 后 v2，版本链才能正确标记废止关系。
"""
import asyncio
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.rag.ingest import ingest_file, parse_policy_md, revoke_old_vectors  # noqa: E402
from app.rag.version_chain import VersionChain  # noqa: E402


async def main():
    data_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "data", "policies")
    files = sorted(glob.glob(os.path.join(data_dir, "*.md")))
    if not files:
        print("data/policies 下没有找到 markdown 文件")
        return

    # 按生效日期排序，保证旧版本先入库
    dated = []
    for f in files:
        meta, _ = parse_policy_md(f)
        dated.append((meta["effective_date"], f, meta))
    dated.sort(key=lambda x: x[0])

    total_chunks = 0
    per_doc_latest: dict[str, str] = {}
    for eff_date, path, meta in dated:
        n = await ingest_file(path)
        total_chunks += n
        per_doc_latest[meta["doc_id"]] = None  # 版本号入库后才知道，稍后查
        print(f"[ingest] {meta['doc_no']} {os.path.basename(path)} -> {n} chunks")

    # 改版过的制度：清掉 Milvus 里旧版本的向量（MongoDB 版本链保留，用于溯源）
    vc = VersionChain()
    for doc_id in per_doc_latest:
        chain = vc.get_chain(doc_id)
        if chain and len(chain["versions"]) > 1:
            current = vc.get_current_version(doc_id)
            await revoke_old_vectors(doc_id, current["version"])
            print(f"[revoke] {doc_id} 旧版本向量已下线，现行版本 {current['version']}")

    print(f"完成：{len(dated)} 个文件，共 {total_chunks} 个条款 chunk")


if __name__ == "__main__":
    asyncio.run(main())
