"""verify_answer 的纯单元测试，不依赖任何外部服务。

运行：pytest tests/test_verify.py -v
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.harness.verify import _parse_citations, check_hash_consistency, verify_answer  # noqa: E402

CHUNKS = [
    {
        "doc_no": "X校发〔2025〕12号", "version": "v2", "clause": "第四条",
        "effective_date": "2025-09-01", "text": "一等奖学金每生每学年4000元……",
    },
    {
        "doc_no": "X校发〔2025〕12号", "version": "v2", "clause": "第八条",
        "effective_date": "2025-09-01", "text": "重修课程达一门及以上……",
    },
]


def test_parse_citations_basic():
    answer = "一等奖学金4000元 [X校发〔2025〕12号 v2 第四条]，比例8%。"
    cites = _parse_citations(answer)
    assert len(cites) == 1
    assert cites[0]["doc_no"] == "X校发〔2025〕12号"
    assert cites[0]["version"] == "v2"
    assert cites[0]["clause"] == "第四条"


def test_parse_citations_multiple():
    answer = ("一等4000元 [X校发〔2025〕12号 v2 第四条]，"
              "重修限制见 [X校发〔2025〕12号 v2 第八条]。")
    assert len(_parse_citations(answer)) == 2


def test_verify_pass():
    answer = "一等奖学金每生每学年4000元，比例不超过8% [X校发〔2025〕12号 v2 第四条]。"
    result = verify_answer(answer, CHUNKS)
    assert result["ok"] is True
    assert result["reason"] == "pass"
    assert len(result["citations"]) == 1


def test_verify_citation_error_wrong_clause():
    # 文号版本都对，但第九条不在检索结果里 → citation_error
    answer = "一等奖学金4000元 [X校发〔2025〕12号 v2 第九条]。"
    result = verify_answer(answer, CHUNKS)
    assert result["ok"] is False
    assert result["bad_spans"][0]["type"] == "citation_error"


def test_verify_hallucination_wrong_doc():
    # 编了一个不存在的文号 → hallucination
    answer = "根据规定，一等奖学金5000元 [X校发〔2024〕99号 v3 第四条]。"
    result = verify_answer(answer, CHUNKS)
    assert result["ok"] is False
    assert result["bad_spans"][0]["type"] == "hallucination"


def test_verify_no_citation_fails():
    result = verify_answer("一等奖学金是4000元哦。", CHUNKS)
    assert result["ok"] is False
    assert result["reason"] == "no_citation"


def test_verify_refusal_passes():
    # 拒答（原文没有依据）不要求引用，算通过
    result = verify_answer("该问题在现有制度中未找到依据，建议咨询教务处。", CHUNKS)
    assert result["ok"] is True
    assert result["reason"] == "refused"


def test_verify_citation_without_clause():
    # 只标文号+版本不标条款，只要该文档版本在检索结果里就算过
    answer = "奖学金标准见相关文件 [X校发〔2025〕12号 v2]。"
    result = verify_answer(answer, CHUNKS)
    assert result["ok"] is True


def test_check_hash_consistency():
    import hashlib
    text = "一等奖学金每生每学年4000元"
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert check_hash_consistency({"text": text}, {"sha256": sha}) is True
    assert check_hash_consistency({"text": text + "（篡改）"}, {"sha256": sha}) is False
