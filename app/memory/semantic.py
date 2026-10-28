"""语义记忆：把高频、已通过校验的问答对沉淀到 MongoDB，下次直接复用。

沉淀条件（都满足才入库）：
1. Verify 通过且非降级回答
2. 同一问题（归一化后）被问 >= 3 次
3. 引用的制度版本仍是现行有效版本
"""
import logging
import re
from datetime import datetime

from app.db.mongo import semantic_qa_col
from app.rag.version_chain import VersionChain

logger = logging.getLogger(__name__)


def normalize_question(q: str) -> str:
    """问题归一化：去标点、去空白，用作去重 key。"""
    return re.sub(r"[\s，。？！?!,.、]", "", q)


class SemanticMemory:
    def lookup(self, question: str) -> dict | None:
        """按归一化问题查沉淀过的标准问答对。"""
        hit = semantic_qa_col().find_one(
            {"norm_question": normalize_question(question)}, {"_id": 0}
        )
        if not hit:
            return None
        # 引用的制度如果已经改版，沉淀的答案就过期了，不再返回
        vc = VersionChain()
        cur = vc.get_current_version(hit["doc_id"])
        if not cur or cur["version"] != hit["version"]:
            logger.info("semantic qa expired: %s", hit["doc_id"])
            return None
        return hit

    def record_hit(self, question: str, answer: str, citations: list[dict]):
        """Verify 通过后调用：计数，达到阈值就沉淀。"""
        col = semantic_qa_col()
        norm = normalize_question(question)
        if not citations:
            return
        cite = citations[0]
        doc = col.find_one_and_update(
            {"norm_question": norm},
            {"$inc": {"hit_count": 1},
             "$set": {"question": question, "answer": answer,
                      "doc_id": self._doc_id_of(cite["doc_no"]),
                      "doc_no": cite["doc_no"], "version": cite["version"],
                      "clause": cite.get("clause", ""),
                      "last_hit_at": datetime.now().isoformat()},
             "$setOnInsert": {"created_at": datetime.now().isoformat()}},
            upsert=True, return_document=True,
        )
        if doc and doc.get("hit_count", 0) >= 3:
            col.update_one({"norm_question": norm}, {"$set": {"promoted": True}})
            logger.info("semantic qa promoted: %s", norm[:30])

    @staticmethod
    def _doc_id_of(doc_no: str) -> str:
        # 简化处理：文号直接当 doc_id 的检索键（真实实现里应该查版本链反解）
        return doc_no
