"""情景记忆：对话超过 N 轮后，用 LLM 把早期轮次压缩成摘要。

解决长对话两个问题：
1. 上下文越来越长，token 爆炸
2. 早期提到的关键信息（比如学生说自己是"大二转专业"）被挤出窗口后遗忘
"""
import logging

from config.settings import get_settings
from app.db.redis_client import get_redis

logger = logging.getLogger(__name__)


class EpisodicMemory:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.r = get_redis()
        self.summary_key = f"em:{session_id}:summary"
        self.summarized_key = f"em:{session_id}:summarized_count"
        self.threshold = get_settings().episodic_summarize_rounds

    def get_summary(self) -> str:
        return self.r.get(self.summary_key) or ""

    def maybe_summarize(self, working_memory) -> bool:
        """轮次超过阈值就触发一次增量摘要。返回是否执行了摘要。"""
        turns = working_memory.get_turns(last_n=50)
        summarized = int(self.r.get(self.summarized_key) or 0)
        new_turns = turns[summarized:]
        if len(turns) < self.threshold or len(new_turns) < self.threshold // 2:
            return False

        # 延迟导入，避免和 harness.nodes 循环依赖
        from app.harness.nodes import get_llm

        dialogue = "\n".join(f"学生：{t['q']}\n助手：{t['a'][:200]}" for t in new_turns)
        old_summary = self.get_summary()
        prompt = (
            "把下面的校内制度问答对话压缩成简短摘要，保留：学生的身份/年级/学院等信息、"
            "咨询过哪些制度、关键结论。不超过 200 字。\n"
            f"已有摘要：{old_summary or '（无）'}\n"
            f"新对话：\n{dialogue}"
        )
        try:
            resp = get_llm(temperature=0).invoke(prompt)
            merged = resp.content.strip()
            self.r.set(self.summary_key, merged)
            self.r.set(self.summarized_key, len(turns))
            return True
        except Exception:  # noqa: BLE001 摘要失败不影响主流程
            logger.warning("episodic summarize failed for %s", self.session_id)
            return False
