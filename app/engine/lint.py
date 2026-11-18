# -*- coding: utf-8 -*-
"""
lint_resume —— 质量闸门（诊断引擎）
====================================
纯规则，不依赖大模型。好处：快、可解释、结果稳定、可累积。
大模型只负责"改写"，判断权留在规则里——这也是产品可以承诺"每条问题都指得出依据"的原因。

四维打分：首屏 25% / 经历价值 35% / 语言专业度 25% / 排版结构 15%
"""
import re

from . import lexicon as LX
from .profile import iter_bullets, all_text

LEVEL_WEIGHT = {"error": 8, "warn": 4, "info": 1.5}

DIM_META = [
    ("firstScreen", "首屏 6 秒", 0.25),
    ("value", "经历价值", 0.35),
    ("language", "语言专业度", 0.25),
    ("layout", "排版与结构", 0.15),
]


def _hits(text, words):
    low = text.lower()
    out = []
    for w in words:
        if w.lower() in low:
            out.append(w)
    return out


def _has_quant(text):
    return bool(LX.QUANT_RE.search(text))


def _has_result(text):
    return bool(_hits(text, LX.RESULT_WORDS))


def _verb_tier(text):
    """返回该 bullet 最强动词等级（L2 > L1 > L0）"""
    for tier in ("L2", "L1", "L0"):
        if _hits(text, LX.VERB_TIERS[tier]):
            return tier
    return "L1"


def estimate_page(profile):
    """按字数估算页数：中文单栏 A4、10.5pt、行高 1.5 约 42 字/行、46 行/页。"""
    chars = 0
    lines = 0
    chars += len(profile["basic"].get("name", "")) * 2
    lines += 4  # 姓名 + 联系方式 + 意向
    for e in profile.get("education") or []:
        lines += 2 + len(" ".join(e.get("notes") or [])) / 42
    for sect in ("internships", "projects"):
        for item in profile.get(sect) or []:
            lines += 1.6  # 标题行
            for b in item.get("bullets") or []:
                t = b.get("text", "")
                chars += len(t)
                lines += max(1, -(-len(t) // 42))
    lines += (len(profile.get("summary") or "") / 42)
    if profile.get("skills"):
        lines += 1 + len(str(profile.get("skills"))) / 42
    for _ in ("教育", "实习", "项目", "技能"):
        lines += 1.4
    pages = lines / 46.0
    return {"lines": round(lines), "pages": round(pages, 2), "overflow": pages > 1.02}


def _parse_ranges(text):
    out = []
    for m in LX.DATE_RANGE_RE.finditer(text or ""):
        y1, m1, y2, m2 = m.group(1), m.group(2), m.group(3), m.group(4)
        a = int(y1) * 12 + int(m1 or 1)
        if y2 in ("至今", "现在", "今"):
            b = 9999 * 12
        else:
            b = int(y2) * 12 + int(m2 or 12)
        if b >= a:
            out.append((a, b, m.group(0)))
    return out


def lint(profile, jd_text=""):
    issues = []
    stats = {"bullets": 0, "quant": 0, "evidence": 0, "weak": 0, "emptyAdj": 0, "colloquial": 0, "result": 0}

    def add(level, rule, problem, fix, bullet_id=None, where=""):
        issues.append({
            "level": level, "rule": rule, "problem": problem, "fix": fix,
            "bulletId": bullet_id, "where": where,
        })

    # ---------------- 1. 逐条 bullet ----------------
    for bid, sect, title, b in iter_bullets(profile):
        text = (b.get("text") or "").strip()
        if not text:
            continue
        stats["bullets"] += 1
        where = f"{title}"
        weak = _hits(text, LX.VERB_TIERS["L0"])
        adj = _hits(text, LX.EMPTY_ADJ)
        col = _hits(text, LX.COLLOQUIAL)
        quant = _has_quant(text)
        result = _has_result(text)
        evi = (b.get("evidence") or "").strip()
        if quant:
            stats["quant"] += 1
        if evi:
            stats["evidence"] += 1
        if result:
            stats["result"] += 1
        if weak:
            stats["weak"] += 1
        if adj:
            stats["emptyAdj"] += 1
        if col:
            stats["colloquial"] += 1

        if weak:
            w = weak[0]
            add("error", "弱动词",
                f"「{w}」是过程性动词，HR 看不出你的贡献边界",
                LX.VERB_UPGRADE.get(w, "换成你实际交付的动作与产出"), bid, where)
        if adj:
            add("warn", "空洞形容词",
                f"含「{'、'.join(adj[:3])}」，这类词无法被验证",
                "删掉，替换成一件具体做过的事 + 结果", bid, where)
        if col:
            add("warn", "口语化",
                f"含口语词「{'、'.join(col[:3])}」",
                "改成书面表达：删掉语气词，句子收在主谓宾上", bid, where)
        if not quant:
            add("warn", "无量化", "整条没有任何数字，价值无法被感知",
                "补：数量（做了多少）/ 周期（多久）/ 幅度（提升多少）/ 范围（覆盖多少人）", bid, where)
        elif not evi:
            add("error", "数字无证据",
                "有数字但没有证据来源，面试被追问口径时会失分",
                "在「证据」里写清来源：后台截图 / 台账 / 报告日期", bid, where)
        if quant and not result:
            add("info", "有数字缺结果", "有数字，但没写这个数字带来了什么",
                "补一句结果：从 X 到 Y、节省多少时间、带来多少转化", bid, where)
        if len(text) > 90:
            add("info", "单条过长", f"{len(text)} 字，超过一行的舒适阅读长度",
                "拆成两条：一条讲做法，一条讲结果", bid, where)
        if len(text) < 12:
            add("warn", "单条过短", "信息量不足，看起来像占位",
                "补上「对象 + 动作 + 结果」三要素", bid, where)
        if _verb_tier(text) == "L0" and not quant:
            add("info", "贡献度模糊", "低能量动词 + 无数字 = 很难判断你是否真的做过",
                "写清你独立负责的那一小块", bid, where)

    # ---------------- 2. 全局规则 ----------------
    text_all = all_text(profile)
    for rx, hint in LX.SENSITIVE_RE:
        if rx.search(text_all):
            add("error", "敏感信息", hint, "删除或脱敏后重新导出（比赛演示务必脱敏）")

    if not (profile["intent"].get("targetRoles") or []):
        add("error", "缺岗位定向", "没有目标岗位，所有改写都是无的放矢", "先贴一个 JD 或选一个岗位方向")

    if not (profile.get("summary") or "").strip():
        add("info", "缺自我评价", "自我评价是首屏唯一能主动定调的位置", "写 3 句：做什么 + 做成过什么 + 想做什么")
    elif len(_hits(profile["summary"], LX.EMPTY_ADJ)) >= 2:
        add("warn", "自我评价是形容词堆砌", "「认真负责、学习能力强」这类句子等于没写",
            "换成：一句定位 + 一个数字 + 一句方向")

    # 首屏：第一段经历的第一条 bullet 决定 HR 会不会继续看
    firsts = []
    for sect in ("internships", "projects"):
        for item in profile.get(sect) or []:
            for b in item.get("bullets") or []:
                if (b.get("text") or "").strip():
                    firsts.append(b["text"])
                    break
            if firsts:
                break
        if firsts:
            break
    if firsts and not _has_quant(firsts[0]):
        add("warn", "首屏无数字", "HR 只看前 6 行，第一段经历的第一条没有数字",
            "把最硬的一个数字提到第一条", None, "首条经历")

    # 时间线冲突
    spans = []
    for sect in ("internships", "projects"):
        for item in profile.get(sect) or []:
            for a, b2, raw in _parse_ranges(item.get("time", "")):
                spans.append((a, b2, raw, item.get("org") or item.get("name") or sect))
    for i in range(len(spans)):
        for j in range(i + 1, len(spans)):
            a1, b1, r1, n1 = spans[i]
            a2, b2, r2, n2 = spans[j]
            if min(b1, b2) - max(a1, a2) >= 2:
                add("warn", "时间线重叠", f"「{n1}」({r1}) 与「{n2}」({r2}) 时间重叠",
                    "如果是兼职/并行，请注明「并行」；否则核对时间是否有误")

    # 日期格式不一致
    fmts = set()
    for sect in ("education", "internships", "projects"):
        for item in profile.get(sect) or []:
            t = item.get("time", "") or ""
            if re.search(r"\d{4}\.\d{1,2}", t):
                fmts.add("dot")
            if re.search(r"\d{4}\s*年", t):
                fmts.add("cn")
            if re.search(r"\d{4}-\d{1,2}", t):
                fmts.add("dash")
            if re.search(r"\d{4}/\d{1,2}", t):
                fmts.add("slash")
    if len(fmts) > 1:
        add("info", "日期格式混用", f"同时出现 {len(fmts)} 种写法", "全篇统一为 2024.06 - 2024.09")

    # 页数
    pg = estimate_page(profile)
    if pg["overflow"]:
        add("warn", "超出一页", f"估算 {pg['pages']} 页（{pg['lines']} 行），超过 A4 一页",
            "优先删：与岗位无关的经历 > 重复的自我评价 > 低价值课程；其次压字号到 10pt、行高 1.4")

    if stats["bullets"] < 4:
        add("warn", "经历条数偏少", f"只有 {stats['bullets']} 条 bullet", "把课程项目、竞赛、社团拆成结构化 bullet")

    # ---------------- 3. 打分 ----------------
    n = max(stats["bullets"], 1)
    quant_rate = stats["quant"] / n
    evi_rate = stats["evidence"] / n
    result_rate = stats["result"] / n
    bad_rate = (stats["weak"] + stats["emptyAdj"] + stats["colloquial"]) / n

    # 首屏
    fs = 40
    if profile["basic"].get("name") and profile["basic"].get("phone"):
        fs += 20
    if profile["intent"].get("targetRoles"):
        fs += 15
    if (profile.get("summary") or "").strip():
        fs += 10
    if firsts and _has_quant(firsts[0]):
        fs += 15
    fs = min(100, fs)

    value = round(min(100, quant_rate * 45 + result_rate * 30 + evi_rate * 25))
    language = round(max(20, min(100, 100 - bad_rate * 70 - (stats["emptyAdj"] / n) * 20)))
    layout = 100
    layout -= 12 if pg["overflow"] else 0
    long_cnt = sum(1 for i in issues if i["rule"] == "单条过长")
    short_cnt = sum(1 for i in issues if i["rule"] == "单条过短")
    layout -= min(24, long_cnt * 4 + short_cnt * 4)
    layout -= 8 if len(fmts) > 1 else 0
    layout = max(30, layout)

    dims = {"firstScreen": fs, "value": value, "language": language, "layout": layout}
    score = round(sum(dims[k] * w for k, _, w in DIM_META))

    comments = {
        "firstScreen": "首屏信息够用，能把事情说清" if fs >= 80 else "HR 前 6 秒抓不到重点，先补意向与第一个数字",
        "value": f"量化率 {round(quant_rate * 100)}% / 结果率 {round(result_rate * 100)}% / 证据率 {round(evi_rate * 100)}%",
        "language": "语言已接近职场书面表达" if language >= 80 else "学生腔明显，动词和形容词是主要扣分项",
        "layout": f"估算 {pg['lines']} 行 / {pg['pages']} 页",
    }

    issues.sort(key=lambda x: -LEVEL_WEIGHT[x["level"]])
    # 去重：同一 bullet 同一规则只留一条
    seen, uniq = set(), []
    for i in issues:
        key = (i["rule"], i["bulletId"], i["problem"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(i)

    grade = "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D"
    return {
        "score": score,
        "grade": grade,
        "dimensions": [
            {"key": k, "name": name, "score": dims[k], "weight": w, "comment": comments[k]}
            for k, name, w in DIM_META
        ],
        "issues": uniq[:40],
        "top3": [i for i in uniq if i["level"] == "error"][:3] or uniq[:3],
        "stats": {
            "bullets": stats["bullets"],
            "quantRate": round(quant_rate * 100),
            "evidenceRate": round(evi_rate * 100),
            "resultRate": round(result_rate * 100),
            "weakRate": round(bad_rate * 100),
            "emptyAdj": stats["emptyAdj"],
            "colloquial": stats["colloquial"],
        },
        "page": pg,
        "issueCount": {
            "error": sum(1 for i in uniq if i["level"] == "error"),
            "warn": sum(1 for i in uniq if i["level"] == "warn"),
            "info": sum(1 for i in uniq if i["level"] == "info"),
        },
    }
