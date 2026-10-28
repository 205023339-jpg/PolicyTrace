"""版本链的单元测试。

用 mongomock 顶替真实 MongoDB，没装 mongomock 就整体跳过：
    pip install mongomock pytest
    pytest tests/test_version_chain.py -v
"""
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

mongomock = pytest.importorskip("mongomock", reason="需要 mongomock 才能跑版本链测试")

import app.rag.version_chain as vc_mod  # noqa: E402
from app.rag.version_chain import VersionChain, compute_sha256  # noqa: E402


@pytest.fixture()
def chain(monkeypatch):
    """把 policies_col 指向 mongomock，并预置一个两版本的奖学金制度。"""
    client = mongomock.MongoClient()
    col = client["policytrace"]["policies"]
    monkeypatch.setattr(vc_mod, "policies_col", lambda: col)

    v = VersionChain()
    v.add_version("jxj", "学生奖学金评定办法", "X校发〔2023〕18号",
                  content="2023版正文：一等奖学金3000元。",
                  effective_date="2023-09-01")
    v.add_version("jxj", "学生奖学金评定办法", "X校发〔2025〕12号",
                  content="2025版正文：一等奖学金4000元。",
                  effective_date="2025-09-01",
                  diff_summary="提高一等奖学金比例和金额")
    return v


def test_add_version_marks_old_revoked(chain):
    rec = chain.get_chain("jxj")
    assert len(rec["versions"]) == 2
    assert rec["versions"][0]["status"] == "revoked"
    assert rec["versions"][0]["revoked_date"] == "2025-09-01"
    assert rec["versions"][1]["status"] == "effective"


def test_version_no_increments(chain):
    rec = chain.get_chain("jxj")
    assert [v["version"] for v in rec["versions"]] == ["v1", "v2"]


def test_get_current_version(chain):
    cur = chain.get_current_version("jxj")
    assert cur["version"] == "v2"
    assert cur["sha256"] == compute_sha256("2025版正文：一等奖学金4000元。")


def test_get_effective_version_old_date(chain):
    # 2024年问这个问题，生效的应该还是 v1
    v = chain.get_effective_version("jxj", date(2024, 10, 1))
    assert v["version"] == "v1"


def test_get_effective_version_boundary(chain):
    # 边界：新版生效日当天归新版，前一天归旧版
    assert chain.get_effective_version("jxj", "2025-08-31")["version"] == "v1"
    assert chain.get_effective_version("jxj", "2025-09-01")["version"] == "v2"


def test_get_effective_version_before_all(chain):
    assert chain.get_effective_version("jxj", date(2020, 1, 1)) is None


def test_get_effective_version_missing_doc(chain):
    assert chain.get_effective_version("no-such-doc", date(2025, 1, 1)) is None


def test_verify_fingerprint(chain):
    assert chain.verify_fingerprint("jxj", "v1", "2023版正文：一等奖学金3000元。") is True
    assert chain.verify_fingerprint("jxj", "v1", "被人改过的正文") is False
    assert chain.verify_fingerprint("jxj", "v9", "2023版正文：一等奖学金3000元。") is False


def test_list_all(chain):
    docs = chain.list_all()
    assert len(docs) == 1
    assert docs[0]["doc_no"] == "X校发〔2023〕18号" or docs[0]["title"]
