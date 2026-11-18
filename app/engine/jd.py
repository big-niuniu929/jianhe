# -*- coding: utf-8 -*-
"""
match_jd —— 岗位定向
====================
把一段 JD 拆成三样东西：
1. 硬门槛（学历/届别/到岗天数/专业/证书）——不满足要早说，别浪费彼此时间
2. 关键词（技能/业务动作）——决定简历里该出现什么词
3. 隐性信号（抗压/0-1/闭环）——决定面试会怎么问
再和档案做交集，算出匹配度与缺口。
"""
import re

from . import lexicon as LX
from .profile import all_text


def _norm(s):
    return (s or "").lower().replace("　", " ")


def extract_keywords(jd_text):
    """从 JD 里抽技能关键词：词库命中 + 英文大写缩写 + 括号内补充。

    每个词带两个形态：`key`（小写，用于匹配）和 `word`（原样，用于展示）。
    分开的原因很实际：JD 里写的是 SQL，简历里写的是 sql，
    匹配必须不分大小写，但显示成小写会显得很不专业。
    """
    low = _norm(jd_text)
    found = {}
    for group, words in LX.SKILL_LIB.items():
        for w in words:
            if w in low:
                found.setdefault(w.lower(), {"word": w, "group": group})
    # 英文缩写（JD 里常见的 SQL/Python/GMV/ROI）
    for m in re.finditer(r"\b([A-Za-z][A-Za-z0-9+#./]{1,12})\b", jd_text or ""):
        raw = m.group(1)
        w = raw.lower()
        if w in {"and", "the", "with", "for", "you", "are", "job", "our", "will"}:
            continue
        if any(w in v for v in LX.SKILL_LIB.get("软件工程", [])) or w in ("sql", "excel", "ppt", "ai", "api", "prd", "roi", "gmv"):
            disp = raw.upper() if len(raw) <= 6 else raw
            found.setdefault(w, {
                "word": disp,
                "group": "工具" if w in ("sql", "excel", "ppt", "api", "prd") else "软件工程",
            })
    # 词库里存的是 sql/excel 这种小写；JD 里如果写的是 SQL，就按 JD 的写法显示。
    # 只影响展示，不影响匹配（匹配一律用小写 key）。
    for k, v in found.items():
        if k.isascii():
            hit = re.search(r"(?<![A-Za-z])" + re.escape(k) + r"(?![A-Za-z])", jd_text or "", re.I)
            if hit:
                v["word"] = hit.group(0)

    # 长词优先：一个词被另一个更长的命中词包含时，去掉短的。
    # 不做这步的话，JD 写「MySQL」会同时抽出 mysql 和 sql，「竞品分析」会同时抽出
    # 竞品分析 和 竞品——同一个要求算两次，分母虚高、缺口列表也重复报。
    # 反过来「JD 只写 SQL」时 sql 不会被谁包含，仍然保留，所以不会漏。
    for k in list(found):
        if any(k != o and k in o for o in found):
            found.pop(k, None)

    return [{"word": v["word"], "key": k, "group": v["group"]} for k, v in found.items()]


def extract_hard_reqs(jd_text):
    out = []
    for rx, name in LX.HARD_REQ_PATTERNS:
        for m in rx.finditer(jd_text or ""):
            frag = m.group(0)
            if any(o["name"] == name and o["text"] == frag for o in out):
                continue
            out.append({"name": name, "text": frag, "hit": True})
    return out


def extract_signals(jd_text):
    low = _norm(jd_text)
    out = []
    for k, v in LX.IMPLICIT_SIGNALS.items():
        if k.lower() in low:
            out.append({"word": k, "why": v})
    return out


def _check_hard(profile, req, resume_text):
    """粗判硬门槛是否满足：能给"满足/需确认/不满足"，不硬判不满足。"""
    low = _norm(resume_text)
    t = req["text"]
    if req["name"] == "学历":
        for e in profile.get("education") or []:
            d = e.get("degree", "")
            if "本科" in t and "本科" in d:
                return "pass", "档案学历为本科"
            if "硕士" in t and ("硕士" in d or "研究生" in d):
                return "pass", "档案学历为硕士"
        if "大专" in t and low:
            return "confirm", "档案未写学历层次"
        return "confirm", "需确认学历是否满足"
    if req["name"] == "院校层次":
        for e in profile.get("education") or []:
            s = e.get("school", "")
            if s:
                return "confirm", f"院校为「{s}」，请自行确认是否属于目标层次"
        return "confirm", "档案未写院校"
    if req["name"] == "到岗天数":
        avail = profile["intent"].get("available", "")
        if avail and ("4" in t or "5" in t) and ("4" in avail or "5" in avail):
            return "pass", f"意向到岗：{avail}"
        return "confirm", f"JD 要求 {t}，请在求职意向里写清可到岗时间"
    if req["name"] == "实习时长":
        avail = profile["intent"].get("available", "")
        return ("pass", f"意向：{avail}") if avail else ("confirm", "请在求职意向里写实习时长")
    if req["name"] == "专业":
        major_words = re.findall(r"(计算机|软件|自动化|电子|数学|统计|市场营销|电子商务|会计|法学)", t)
        for e in profile.get("education") or []:
            if any(w in (e.get("major") or "") for w in major_words):
                return "pass", f"专业匹配：{e.get('major')}"
        return "confirm", "专业不完全对口，需要用项目/实习证明能力"
    if req["name"] in ("经验年限", "届别", "英语等级"):
        return ("confirm", f"JD 写有「{t}」，请在简历中明确对应信息")
    return "confirm", "需人工确认"


def _collab_only(jd_text, word):
    """判断这个词是否只出现在「与产品、算法、设计协作」这种并列里——
    那是团队名不是技能要求，不该算进简历缺口。"""
    if not word:
        return False
    low = _norm(jd_text)
    idx = low.find(word)
    if idx < 0:
        return False
    window = low[max(0, idx - 14): idx + 14]
    return bool(re.search(r"[与和跟同]", window)) and bool(re.search(r"(协作|配合|对接|沟通|联动|协同)", window))


def match(profile, jd_text):
    jd_text = jd_text or ""
    resume_text = all_text(profile)
    low_resume = _norm(resume_text)

    kws = extract_keywords(jd_text)
    hard = extract_hard_reqs(jd_text)
    signals = extract_signals(jd_text)

    covered, missing = [], []
    for k in kws:
        if k["key"] in low_resume:
            covered.append(k)
        elif _collab_only(jd_text, k["key"]):
            continue  # 只做协作对象出现的词，不算缺口
        else:
            missing.append(k)

    for h in hard:
        status, why = _check_hard(profile, h, resume_text)
        h["status"] = status
        h["why"] = why

    kw_rate = (len(covered) / len(kws)) if kws else 0
    hard_pass = sum(1 for h in hard if h["status"] == "pass")
    hard_confirm = sum(1 for h in hard if h["status"] == "confirm")
    hard_total = max(len(hard), 1)
    # 硬门槛：pass 记满分，confirm 记 0.5（因为不确定不等于不满足）
    hard_score = (hard_pass + hard_confirm * 0.5) / hard_total if hard else 0.8

    score = round((kw_rate * 0.6 + hard_score * 0.4) * 100)

    # 缺失关键词按业务价值排序（业务词优先于工具词）
    prio = {"电商运营": 0, "数据分析": 1, "产品": 1, "AI": 0, "职场通用": 2, "软件工程": 2, "证书语言": 3, "工具": 3}
    missing.sort(key=lambda x: prio.get(x["group"], 9))

    return {
        "score": score,
        "level": "强匹配" if score >= 75 else "可投" if score >= 55 else "需补强" if score >= 35 else "不建议",
        "keywords": {"total": len(kws), "covered": len(covered), "missing": missing[:12], "hit": covered[:20]},
        "hardReqs": hard,
        "signals": signals[:6],
        "advice": _advice(score, missing, hard),
    }


def _advice(score, missing, hard):
    tips = []
    if missing:
        words = "、".join(x["word"] for x in missing[:4])
        tips.append(f"JD 里有「{words}」，简历全篇没出现——如果真做过，补进经历；没做过，别硬塞。")
    bad = [h for h in hard if h["status"] == "confirm" and h["name"] in ("学历", "到岗天数", "实习时长")]
    if bad:
        tips.append("硬门槛有未确认项（" + "、".join(h["name"] for h in bad) + "），投递前在求职意向里写清，能减少无效沟通。")
    if score < 55:
        tips.append("当前匹配度偏低，建议先用「定向改写」把经历往 JD 的关键词与结果上重排，再决定投不投。")
    else:
        tips.append("匹配度达标，重点变成：让每条经历都对准 JD 的关键词，并把数字补上证据。")
    return tips
