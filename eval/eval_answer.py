"""准确率评测脚本：跑评测集 → 调 /api/ask → LLM judge + 引用校验 → 输出指标。

用法：
    python eval/eval_answer.py                    # 跑全部样例
    python eval/eval_answer.py --limit 10         # 只跑前10条（调试用）
    python eval/eval_answer.py --role student     # 只跑某个角色的用例

指标口径：
- 回答准确率：LLM judge 判断答案是否正确回应了问题要点（对照 expected_answer_points）
- 引用正确率：答案中的引用与 expected_doc_no/version/clause 完全一致的比例
  （只统计"应该给出引用"的题目；权限拒答类题目引用正确率不计入）
"""
import argparse
import asyncio
import json
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

API = os.environ.get("PT_API", "http://localhost:8000")
DATASET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval_dataset.json")


def load_dataset(limit: int | None, role: str | None) -> list[dict]:
    data = json.load(open(DATASET, encoding="utf-8"))
    if role:
        data = [d for d in data if d["role"] == role]
    if limit:
        data = data[:limit]
    return data


async def ask_once(client: httpx.AsyncClient, item: dict) -> dict:
    resp = await client.post(f"{API}/api/ask", json={
        "question": item["question"], "role": item["role"],
    }, timeout=60)
    resp.raise_for_status()
    return resp.json()


async def llm_judge(item: dict, answer: str) -> bool:
    """LLM judge：答案是否覆盖了评分要点。"""
    from app.harness.nodes import get_llm
    points = "\n".join(f"- {p}" for p in item["expected_answer_points"])
    prompt = (
        "你是评测员。判断【回答】是否正确覆盖了【评分要点】，"
        '只输出 JSON {"pass": true/false}。\n'
        f"【问题】{item['question']}\n【评分要点】\n{points}\n【回答】\n{answer}"
    )
    resp = await get_llm(temperature=0).ainvoke(prompt)
    try:
        return json.loads(resp.content).get("pass", False)
    except json.JSONDecodeError:
        return "true" in resp.content.lower()


def citation_ok(item: dict, result: dict) -> bool:
    """引用校验：文号+版本+条款必须和标注一致。"""
    cites = result.get("citations", [])
    if not cites:
        # 权限类题目允许"拒答/给摘要"，不算引用错误
        return item.get("type") == "permission"
    c = cites[0]
    return (c.get("doc_no") == item["expected_doc_no"]
            and c.get("version") == item["expected_version"]
            and (not item.get("expected_clause")
                 or c.get("clause") == item["expected_clause"]))


async def run(limit: int | None, role: str | None):
    dataset = load_dataset(limit, role)
    async with httpx.AsyncClient() as client:
        tasks = [ask_once(client, item) for item in dataset]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    answer_pass = citation_pass = counted = errors = 0
    badcases = []
    for item, result in zip(dataset, results):
        if isinstance(result, Exception):
            errors += 1
            badcases.append({"id": item["id"], "reason": f"request error: {result}"})
            continue
        counted += 1
        ok_answer = await llm_judge(item, result["answer"])
        ok_cite = citation_ok(item, result)
        answer_pass += ok_answer
        citation_pass += ok_cite
        if not (ok_answer and ok_cite):
            badcases.append({
                "id": item["id"], "question": item["question"],
                "answer_pass": ok_answer, "citation_pass": ok_cite,
                "trace_id": result.get("trace_id"),
            })

    print(f"总数: {len(dataset)}  成功: {counted}  请求失败: {errors}")
    if counted:
        print(f"回答准确率: {answer_pass / counted:.1%} ({answer_pass}/{counted})")
        print(f"引用正确率: {citation_pass / counted:.1%} ({citation_pass}/{counted})")
    out = os.path.join(os.path.dirname(DATASET), "eval_badcases.json")
    json.dump(badcases, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"badcase 明细已写入 {out}，可用 scripts/replay_trace.py 回放")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--role", default=None, choices=["student", "teacher", "admin"])
    args = parser.parse_args()
    asyncio.run(run(args.limit, args.role))
