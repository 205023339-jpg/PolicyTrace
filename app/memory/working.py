"""工作记忆：Redis 存当前会话的槽位和最近对话轮次，TTL 30 分钟。

key 设计：
  wm:{session_id}:slots   hash，存槽位（last_query、last_doc_no 等）
  wm:{session_id}:turns   list，存最近对话轮次的 JSON
"""
import json

from config.settings import get_settings
from app.db.redis_client import get_redis


class WorkingMemory:
    def __init__(self, session_id: str):
        self.r = get_redis()
        self.ttl = get_settings().working_memory_ttl
        self.slots_key = f"wm:{session_id}:slots"
        self.turns_key = f"wm:{session_id}:turns"

    # ---- 槽位 ----
    def set_slot(self, name: str, value: str):
        self.r.hset(self.slots_key, name, value)
        self.r.expire(self.slots_key, self.ttl)

    def get_slots(self) -> dict:
        return self.r.hgetall(self.slots_key) or {}

    # ---- 对话轮次 ----
    def append_turn(self, question: str, answer: str):
        turn = json.dumps({"q": question, "a": answer}, ensure_ascii=False)
        self.r.rpush(self.turns_key, turn)
        self.r.ltrim(self.turns_key, -50, -1)  # 最多留 50 轮，防止无限膨胀
        self.r.expire(self.turns_key, self.ttl)

    def get_turns(self, last_n: int = 10) -> list[dict]:
        raw = self.r.lrange(self.turns_key, -last_n, -1)
        return [json.loads(t) for t in raw]

    def clear(self):
        self.r.delete(self.slots_key, self.turns_key)
