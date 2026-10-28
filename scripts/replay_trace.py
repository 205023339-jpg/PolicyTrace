"""命令行回放某个 trace，看每个节点的执行情况。

用法：
    python scripts/replay_trace.py <trace_id>
    python scripts/replay_trace.py --recent 5   # 列出最近5条trace再挑一个看
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.feedback.trace_store import list_recent_traces, load_trace  # noqa: E402


def print_trace(trace: dict):
    print("=" * 70)
    print(f"trace_id : {trace['trace_id']}")
    print(f"时间     : {trace.get('created_at', '?')}")
    print(f"角色     : {trace.get('role', '?')}")
    print(f"问题     : {trace.get('question', '?')}")
    print("-" * 70)
    print("节点执行轨迹：")
    for i, step in enumerate(trace.get("steps", []), 1):
        status = step.get("status", "?")
        mark = "OK " if status == "ok" else "!! "
        print(f"  {i}. [{mark}] {step.get('node', '?'):<10} status={status}")
    result = trace.get("result", {})
    print("-" * 70)
    print(f"校验状态 : {result.get('verify_status', '?')}")
    print(f"是否降级 : {result.get('degraded', False)}")
    print(f"引用     : {result.get('citations', [])}")
    print(f"回答     :\n{result.get('answer', '(无)')}")
    print("=" * 70)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    if args[0] == "--recent":
        n = int(args[1]) if len(args) > 1 else 5
        for t in list_recent_traces(limit=n):
            print(f"{t['created_at']}  {t['trace_id']}  [{t['role']}] {t['question'][:40]}")
        return

    trace = load_trace(args[0])
    if trace is None:
        print(f"trace 不存在: {args[0]}")
        sys.exit(1)
    print_trace(trace)


if __name__ == "__main__":
    main()
