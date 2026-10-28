"""ContextBuilder：把检索片段、情景摘要、最近对话组装成最终 prompt 上下文。

三个约束，按顺序执行：
1. 权限：角色看不到的字段在 retriever 层已经处理，这里再兜一层（防止内部细则泄漏）
2. 时效：同一制度多个片段时，生效日期新的排前面；已废止版本直接丢弃
3. Token 预算：用 tiktoken 数 token，超预算从后往前裁剪，
   但保证每个制度的"第一条"（总则）尽量保留
"""
import logging

import tiktoken

from config.settings import get_settings
from app.memory.episodic import EpisodicMemory
from app.memory.working import WorkingMemory

logger = logging.getLogger(__name__)

try:
    _enc = tiktoken.encoding_for_model("gpt-4o")
except Exception:  # noqa: BLE001 离线环境可能拉不到编码文件，退化用 cl100k
    _enc = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_enc.encode(text))


class ContextBuilder:
    def __init__(self, role: str, token_budget: int | None = None):
        self.role = role
        self.budget = token_budget or get_settings().context_token_budget

    def build(self, question: str, chunks: list[dict], session_id: str) -> str:
        parts = []

        # 1. 情景记忆摘要（长对话时防止遗忘早期信息）
        summary = EpisodicMemory(session_id).get_summary()
        if summary:
            parts.append(f"[对话背景摘要]\n{summary}")

        # 2. 最近 3 轮对话原文（工作记忆）
        recent = WorkingMemory(session_id).get_turns(last_n=3)
        if recent:
            lines = [f"学生：{t['q']}\n助手：{t['a'][:150]}" for t in recent]
            parts.append("[最近对话]\n" + "\n".join(lines))

        # 3. 检索片段：按时效排序（生效日期新的在前），废止版本兜底丢弃
        sorted_chunks = sorted(
            (c for c in chunks if c.get("status", "effective") != "revoked"),
            key=lambda c: c.get("effective_date", ""), reverse=True,
        )
        ctx_chunks = []
        used = sum(count_tokens(p) for p in parts)
        for c in sorted_chunks:
            block = self._format_chunk(c)
            t = count_tokens(block)
            if used + t > self.budget:
                # 超预算：条款正文截一半再试一次，还不行就跳过
                block = self._format_chunk(c, truncate=True)
                t = count_tokens(block)
                if used + t > self.budget:
                    logger.debug("chunk dropped by token budget: %s %s",
                                 c.get("doc_no"), c.get("clause"))
                    continue
            ctx_chunks.append(block)
            used += t

        parts.append("[制度原文]\n" + "\n".join(ctx_chunks))
        return "\n\n".join(parts)

    def _format_chunk(self, c: dict, truncate: bool = False) -> str:
        text = c.get("text", "")
        if truncate and len(text) > 200:
            text = text[:200] + "……（内容因长度限制被截断）"
        # 权限兜底：student 看不到【内部细则】之后的内容
        if self.role == "student" and "【内部细则】" in text:
            text = text.split("【内部细则】")[0]
        header = f"《{c.get('title', '')}》{c.get('clause', '')}（{c.get('doc_no', '')} {c.get('version', '')} 生效:{c.get('effective_date', '')}）"
        return f"{header}\n{text}"
