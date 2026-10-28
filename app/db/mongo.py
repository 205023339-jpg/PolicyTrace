"""MongoDB 客户端封装，整个项目共用一个连接。"""
from functools import lru_cache

from pymongo import MongoClient
from pymongo.database import Database

from config.settings import get_settings


@lru_cache
def get_mongo_db() -> Database:
    s = get_settings()
    client = MongoClient(s.mongo_uri, serverSelectionTimeoutMS=3000)
    return client[s.mongo_db]


def policies_col():
    """制度版本链集合：一个 doc_id 一条记录，里面挂 versions 数组。"""
    return get_mongo_db()["policies"]


def semantic_qa_col():
    """语义记忆：沉淀下来的高频问答对。"""
    return get_mongo_db()["semantic_qa"]


def trace_col():
    """trace 落盘集合，支持回放。"""
    return get_mongo_db()["traces"]
