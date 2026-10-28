"""全局配置，用 pydantic-settings 从环境变量 / .env 读取。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    llm_api_key: str = "sk-placeholder"
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus"

    # 存储
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "policytrace"
    redis_url: str = "redis://localhost:6379/0"
    milvus_uri: str = "http://localhost:19530"
    milvus_collection: str = "policy_chunks"

    # 检索 / 记忆参数（迭代过程中经常调，所以放配置里）
    embedding_dim: int = 1024
    retrieval_top_k: int = 6
    rrf_weight_vector: float = 0.7      # 向量召回在 RRF 融合里的权重
    rrf_weight_keyword: float = 0.3     # 关键词召回权重
    working_memory_ttl: int = 1800      # 工作记忆 30 分钟
    episodic_summarize_rounds: int = 8  # 超过 8 轮触发情景记忆摘要
    context_token_budget: int = 3000    # ContextBuilder 的 token 预算

    # Harness 超时（秒）
    node_timeout_intent: int = 5
    node_timeout_rewrite: int = 8
    node_timeout_retrieval: int = 10
    node_timeout_answer: int = 25
    node_timeout_verify: int = 8
    max_verify_retry: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()
