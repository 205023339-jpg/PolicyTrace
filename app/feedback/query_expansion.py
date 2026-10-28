"""查询扩展：jieba 分词 + 校园场景同义词表 + LLM 改写，生成多个查询变体。

迭代日志里 v0.2 的主要改动之一：解决"学生口语说法和制度书面用语对不上"
导致的检索未命中（比如"挂科了还能评奖学金吗" vs "课程考核不合格 奖学金评定资格"）。
"""
import jieba

# 校园场景同义词表：口语 -> 制度书面用语（badcase 分析里逐步积累下来的）
SYNONYMS = {
    "挂科": ["课程考核不合格", "考试不及格"],
    "奖学金": ["奖助学金", "奖学金评定"],
    "请假": ["请假审批", "休假管理"],
    "逃课": ["旷课"],
    "宿舍": ["学生公寓", "住宿"],
    "转专业": ["专业调整", "转专业申请"],
    "保研": ["推荐免试研究生"],
    "毕业证": ["毕业证书", "学历授予"],
    "处分": ["纪律处分", "违纪处理"],
    "借书": ["图书借阅"],
    "补考": ["课程重修", "补考申请"],
    "综测": ["综合素质测评"],
}


def expand_by_synonyms(query: str) -> str:
    """jieba 分词后把命中的口语词替换成书面语，生成一个变体。"""
    words = list(jieba.cut(query))
    replaced = []
    hit = False
    for w in words:
        if w in SYNONYMS:
            replaced.append(SYNONYMS[w][0])
            hit = True
        else:
            replaced.append(w)
    return "".join(replaced) if hit else ""


async def expand_by_llm(query: str) -> list[str]:
    """LLM 生成 2 个书面化查询变体。"""
    from app.harness.nodes import get_llm
    prompt = (
        "把这个学生提问改写成 2 个适合检索高校制度文件的规范查询，"
        "每行一个，不要编号不要解释。\n"
        f"提问：{query}"
    )
    try:
        resp = await get_llm(temperature=0.3).ainvoke(prompt)
        return [l.strip() for l in resp.content.splitlines() if l.strip()][:2]
    except Exception:  # noqa: BLE001 LLM 挂了就只用同义词扩展
        return []


async def expand_query(query: str) -> list[str]:
    """返回去重后的查询列表，原始查询永远排第一。"""
    variants = [query]
    syn = expand_by_synonyms(query)
    if syn and syn != query:
        variants.append(syn)
    variants.extend(await expand_by_llm(query))
    # 去重保序
    seen, result = set(), []
    for v in variants:
        if v not in seen:
            seen.add(v)
            result.append(v)
    return result
