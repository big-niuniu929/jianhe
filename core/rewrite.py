# -*- coding: utf-8 -*-
"""
rewrite_bullet —— 改写引擎
==========================
两种模式：
- rules（默认，离线可用）：去口语 → 动词升级 → 结果补位 → 三版侧重
- llm（配置 API Key 后启用）：严格约束「不新增事实」，输出 3 版 + 改动说明

无论哪种模式，最后都过一次 `fact_guard`：输出里的数字必须都能在原文找到，
否则标记为「疑似新增事实」——这是产品红线，不交给模型自觉。
"""
import re

from . import lexicon as LX

# 动词升级（保守版：只在语义不失真的前提下替换）
UPGRADE_SAFE = [
    (r"参与(了|到)?", "承接"),
    (r"协助(了)?", "配合推进"),
    (r"帮忙(做|写|整理)?", "交付"),
    (r"帮助", "支持"),
    (r"配合", "对接"),
    (r"负责(了)?", "独立负责"),
    (r"完成了", "交付"),
    (r"做了", "完成"),
    (r"跟进", "推进"),
    (r"整理(了)?", "建立"),
    (r"处理(了)?", "处置"),
    (r"学习(了)?", "上手"),
    (r"了解了", "梳理"),
]

UPGRADE_RE = re.compile("|".join("(?:%s)" % p for p, _ in UPGRADE_SAFE))

COLLOQ_FIX = [    ("然后", ""), ("还有", ""), ("挺好的", "反馈良好"), ("我觉得", ""),
    ("我感觉", ""), ("其实", ""), ("差不多", "约"), ("一些", ""),
    ("什么的", ""), ("之类的", ""), ("之类", ""), ("反正", ""),
    ("基本上", ""), ("应该是", "为"), ("搞", "推进"), ("弄", "整理"),
    ("好多", "多个"), ("非常多", "大量"), ("特别", ""), ("超级", ""),
    ("蛮", "较"), ("的时候", "期间"), ("了很多", ""),
]

TAIL_TEMPLATES = {
    "A": "（补：这个动作带来的结果——从 X 到 Y / 节省 Z）",
    "B": "（补：口径——统计周期、样本量、对比基准）",
    "C": "（补：沉淀物——模板/规则/文档，现在还在被谁用）",
}


def _clean(text):
    t = text
    for a, b in COLLOQ_FIX:
        t = t.replace(a, b)
    t = re.sub(r"[，,]{2,}", "，", t)
    t = re.sub(r"^[，,。\s]+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _upgrade_verbs(text):
    """只升级「第一个」弱动词：大面积替换会把句子改成不像人话（链式替换尤其危险）。"""
    done = [False]
    changed = []

    def rep(m):
        if done[0]:
            return m.group(0)
        for pat, r in UPGRADE_SAFE:
            try:
                if re.fullmatch(pat, m.group(0)):
                    done[0] = True
                    changed.append(m.group(0))
                    return r
            except re.error:
                continue
        return m.group(0)

    try:
        t = UPGRADE_RE.sub(rep, text)
    except re.error:
        return text, []
    return t, changed


def _split_clauses(text):
    parts = re.split(r"[，,；;。]", text)
    return [p.strip() for p in parts if p.strip()]


def _numbers(text):
    return set(m.group(0).strip() for m in LX.QUANT_RE.finditer(text or ""))


def fact_guard(original, rewritten):
    """改写不得新增数字：输出中的数字必须能追溯到原文。"""
    src = set(re.findall(r"\d+(?:\.\d+)?", original or ""))
    tgt = set(re.findall(r"\d+(?:\.\d+)?", rewritten or ""))
    new = sorted(tgt - src)
    return {"ok": not new, "newNumbers": new}


def rewrite_rules(text, jd_words=None, evidence=""):
    """规则版改写：产出 A/B/C 三版。"""
    base = _clean(text)
    upgraded, verbs = _upgrade_verbs(base)
    clauses = _split_clauses(upgraded)
    has_quant = bool(LX.QUANT_RE.search(upgraded))
    has_evi = bool((evidence or "").strip())

    # 动词前置：如果开头不是动词（常见于"我参与了…"），裁掉主语
    head = re.sub(r"^(我|我们|本人)", "", upgraded).strip()

    A = head
    if not has_quant:
        A = A.rstrip("。") + TAIL_TEMPLATES["A"]
    B = head
    any_num = next((x for tup in LX.QUANT_RE.findall(head) for x in tup if x), "")
    if any_num:
        B = head.replace(any_num, f"{any_num}（口径：__）", 1).rstrip("。") + "，口径与统计周期可复核"
    else:
        B = head.rstrip("。") + TAIL_TEMPLATES["B"]
    C = f"{head.rstrip('。')}，并把做法沉淀为可复用流程" + ("" if has_quant else TAIL_TEMPLATES["C"])

    versions = []
    for key, txt, why in (
        ("A", A, "结果导向：把动作链收在结果上，动词换成有决策意味的版本"),
        ("B", B, "数据导向：数字前置，并主动交代口径（面试官最常追问的就是口径）"),
        ("C", C, "沉淀导向：强调方法与可复用产物，适合强调「有体系」的岗位"),
    ):
        g = fact_guard(text, txt)
        versions.append({
            "version": key, "text": txt, "why": why,
            "guard": g,
            "changed": verbs,
            "evidence": evidence or "",
            "evidenceStatus": "verified" if has_evi else "missing",
        })
    return versions


def rewrite_llm(text, jd_words, evidence, llm_call):
    """大模型版改写：强约束不新增事实。llm_call(prompt) -> str"""
    prompt = f"""你是资深简历改写专家。只做改写，不做创作。

【任务】把下面这条经历改写成 3 个版本，每个版本 ≤ 80 字，以强动词开头。
【硬约束】
1. 不得新增原文没有的事实、数字、公司名、时间；
2. 原文没有的数字，一律用【待补】占位，不要自己编；
3. 不得删除原文的真实动作，只能重组顺序、替换动词、压缩冗余；
4. 目标岗位关键词（若有）：{"、".join(jd_words or []) or "未提供"}

【原文】{text}
【证据字段】{evidence or "（空）"}

【输出】严格 JSON 数组，不要任何解释文字：
[{{"version":"A","text":"...","why":"改了什么、为什么"}},
  {{"version":"B","text":"...","why":"..."}},
  {{"version":"C","text":"...","why":"..."}}]
"""
    raw = llm_call(prompt)
    import json
    try:
        s = raw[raw.index("["): raw.rindex("]") + 1]
        arr = json.loads(s)
    except Exception:
        return rewrite_rules(text, jd_words, evidence), "llm_parse_failed"
    out = []
    for item in arr[:3]:
        g = fact_guard(text, item.get("text", ""))
        out.append({
            "version": item.get("version", "?"),
            "text": item.get("text", ""),
            "why": item.get("why", ""),
            "guard": g,
            "evidence": evidence or "",
            "evidenceStatus": "verified" if (evidence or "").strip() else "missing",
        })
    return out, "llm"
