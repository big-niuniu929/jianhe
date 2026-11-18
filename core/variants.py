# -*- coding: utf-8 -*-
"""
一岗一版 —— 版本的命名、对比与缺口汇总
======================================
为什么需要这个模块：job hunting 的真实形态不是"一份简历投所有岗"，
而是「投 30 家 = 30 次微调」。用户真正愿意付钱的，是**第 2 份到第 30 份**。

所以这里只做三件事，都围绕"别让用户重复劳动"：
  1. 自动命名 —— 让用户在 30 个版本里一眼找到那一个
  2. 差异对比 —— 让他知道"这版和通用版到底差在哪"，而不是重读一遍
  3. 缺口汇总 —— 让他知道"这版还差什么才能投"

刻意不做的事：
  · 不做版本合并（会制造"两份都不对"的第三份）
  · 不做自动改写（改写要给人看、要人确认，属于 tailor 的职责）
"""
import re

from . import jd as jd_mod

# 「岗位：xxx」「职位名称：xxx」「【公司】」
_ROLE_IN_JD = re.compile(r"(?:岗位|职位|职位名称|招聘岗位)\s*[:：]\s*([^\n，,。；;（(\[]{2,30})")
_COMPANY_IN_JD = re.compile(r"(?:公司|企业|公司名称)\s*[:：]\s*([^\n，,。；;（(\[]{2,30})")
_BRACKET = re.compile(r"[【\[]([^】\]]{2,20})[】\]]")


def guess_role(jd="", fallback=""):
    m = _ROLE_IN_JD.search(jd or "")
    if m:
        return m.group(1).strip()
    if fallback:
        return fallback.strip()
    return ""


def guess_company(jd=""):
    m = _COMPANY_IN_JD.search(jd or "")
    if m:
        return m.group(1).strip()
    m = _BRACKET.search(jd or "")
    if m:
        return m.group(1).strip()
    return ""


def compose_name(company="", role="", jd="", seq=1):
    """
    生成显示名。优先级：公司·岗位 > 岗位 > JD 里能猜到的 > 版本 N。
    名字是给人看的，重复了不好认，所以带上序号兜底。
    """
    c = (company or "").strip()
    r = (role or guess_role(jd)).strip()
    # 名字是给人扫一眼的，超过 24 字在版本条上就变成省略号，等于没名字
    name = f"{c} · {r}" if (c and r) else (r or c or f"版本 {seq}")
    return name if len(name) <= 24 else name[:23] + "…"


def _flatten(profile):
    """把档案里的所有"会被 HR 读到的一句话"摊平成 {bid: {...}}，用于逐条对比。"""
    out = {}

    def push(sect, i, j, item_label, text):
        out[f"{sect}.{i}.{j}"] = {
            "bid": f"{sect}.{i}.{j}",
            "sect": sect,
            "label": item_label,
            "text": (text or "").strip(),
        }

    for sect, name_key, cn in (("internships", "org", "实习"), ("projects", "name", "项目")):
        for i, it in enumerate(profile.get(sect) or []):
            label = f"{cn} · {it.get(name_key) or '未命名'}"
            for j, b in enumerate(it.get("bullets") or []):
                push(sect, i, j, label, b.get("text"))
    return out


def diff(a, b):
    """
    对比两份档案。a 是基准（通常是通用版），b 是这一版。
    只报"用户能看懂的三类"：改了哪句、加了哪句、删了哪句。
    技能/自我评价单独给，因为它们的改法不一样（不是逐条，而是整体替换）。
    """
    fa, fb = _flatten(a or {}), _flatten(b or {})
    changed, added, removed = [], [], []
    for bid, it in fb.items():
        old = fa.get(bid)
        if old is None:
            added.append(it)
        elif old["text"] != it["text"]:
            changed.append(dict(it, before=old["text"], after=it["text"]))
    for bid, it in fa.items():
        if bid not in fb:
            removed.append(it)

    meta = []
    sa, sb = (a or {}).get("summary") or "", (b or {}).get("summary") or ""
    if sa != sb:
        meta.append({"what": "自我评价", "before": sa, "after": sb})
    ta = [g.get("group") for g in ((a or {}).get("skills") or [])]
    tb = [g.get("group") for g in ((b or {}).get("skills") or [])]
    if ta != tb:
        meta.append({"what": "技能分组",
                     "before": " / ".join(x or "" for x in ta),
                     "after": " / ".join(x or "" for x in tb)})
    ra = (a or {}).get("intent", {}).get("targetRoles") or []
    rb = (b or {}).get("intent", {}).get("targetRoles") or []
    if ra != rb:
        meta.append({"what": "求职意向", "before": " / ".join(ra), "after": " / ".join(rb)})

    return {
        "changed": changed, "added": added, "removed": removed, "meta": meta,
        "stats": {"changed": len(changed), "added": len(added),
                  "removed": len(removed), "meta": len(meta)},
        "same": not (changed or added or removed or meta),
    }


def gap(profile, jd_text):
    """
    缺口汇总。不重新算一套算法，直接复用 jd.match —— 同一个数在页面两个地方
    显示成两个值，是最容易被用户抓到的"不专业"。
    """
    if not (jd_text or "").strip():
        return {"hasJd": False, "score": None, "missing": [], "missingCount": 0,
                "covered": 0, "total": 0, "advice": []}
    m = jd_mod.match(profile or {}, jd_text)
    return {
        "hasJd": True,
        "score": m["score"],
        "level": m["level"],
        "missing": [k["word"] for k in m["keywords"]["missing"]],
        "missingCount": len(m["keywords"]["missing"]),
        "covered": m["keywords"]["covered"],
        "total": m["keywords"]["total"],
        "advice": m["advice"],
    }


def plan(profile, jd_text):
    """
    这一版"投不投得出去"的一句话判断 + 三个动作。
    规则版，不烧算力：给的是"该动哪"，不是"改成什么"。
    """
    g = gap(profile, jd_text)
    if not g["hasJd"]:
        return {"verdict": "这版还没绑 JD，先贴一段岗位描述，才知道该往哪改。",
                "todos": ["贴 JD", "跑一次诊断"], "gap": g}
    todos = []
    if g["missing"]:
        todos.append("JD 里有这几个词你简历没出现：" + "、".join(g["missing"][:5]))
    if g["score"] < 55:
        todos.append("匹配度偏低，先重排经历顺序：把最接近 JD 的那段提到最前面")
    if not todos:
        todos.append("关键词已覆盖，接下来补证据：每个数字挂一条来源")
    return {
        "verdict": f"这一版对这段 JD 是 {g['score']} 分（{g.get('level','')}）。",
        "todos": todos, "gap": g,
    }


def ready(v, gap_info=None):
    """版本条上的状态标签，只用三种：已投过 / 可投 / 还差一点。"""
    if (v.get("exports") or 0) > 0:
        return "已投过", "done"
    if gap_info and gap_info.get("hasJd") and gap_info.get("score") is not None:
        return ("可投", "ok") if gap_info["score"] >= 55 else ("还差一点", "warn")
    return ("草稿", "draft")


# ---------------------------------------------------------------- 规则版定制
_WEAK_VERB = re.compile(r"^(参与|协助|帮忙|配合|负责一些|跟过|做过|了解|学习|接触)")


def doc_bullets(profile):
    out = []
    for sect, name_key, cn in (("internships", "org", "实习"), ("projects", "name", "项目")):
        for i, it in enumerate(profile.get(sect) or []):
            label = f"{cn} · {it.get(name_key) or '未命名'}"
            for j, b in enumerate(it.get("bullets") or []):
                out.append({"bid": f"{sect}.{i}.{j}", "text": (b.get("text") or "").strip(),
                            "label": label, "sect": sect})
    return out


def tailor_rules(profile, jd_text, missing=None, limit=3):
    """
    不烧算力版的"按 JD 定制"。
    它不做文学创作，只做一件事：**把最该动的那几句挑出来，给出方向**。
    这也解释了我们为什么敢在没接大模型时照样卖 4.9：免费版给的是"该改哪"，
    付费版给的是"改成什么"，边界清楚，用户不会觉得被糊弄。
    """
    from . import rewrite as rw

    bs = doc_bullets(profile)
    miss = list(missing or [])

    scored = []
    for b in bs:
        s = 0
        has_num = bool(re.search(r"\d", b["text"]))
        weak = bool(_WEAK_VERB.match(b["text"]))
        if not has_num:
            s += 3
        if len(b["text"]) < 24:
            s += 2
        if weak:
            s += 2
        if not any(w.lower() in b["text"].lower() for w in miss):
            s += 1
        # 已经有数字、开头也不是弱动词的句子，别去动它 —— 改了容易越界加事实
        if s and (not has_num or weak):
            scored.append((s, b))
    scored.sort(key=lambda x: -x[0])

    moves = []
    for _s, b in scored[:limit]:
        versions = rw.rewrite_rules(b["text"], miss)
        after = versions[0]["text"] if versions else b["text"]
        covers = [w for w in miss
                  if w.lower() in after.lower() and w.lower() not in b["text"].lower()]
        moves.append({
            "bid": b["bid"], "label": b["label"],
            "before": b["text"], "after": after,
            "covers": covers,
            "why": "这条缺结果、缺口径，先把动作收在结果上；不要动词堆砌。",
            "verify": "改后括号里的结果/口径必须换成你真实的数据，没数据就删掉括号。",
            "guard": rw.fact_guard(b["text"], after),
        })

    blob = " ".join(b["text"] for b in bs).lower()
    absent = [w for w in miss if w.lower() not in blob]
    # 一句话说完，而不是每个词刷一遍同样的提醒：
    # 手机上 8 条重复文案会把整屏占满，用户反而看不到上面的"该改哪几句"。
    not_claimable = []
    if absent:
        not_claimable.append(
            "JD 要「" + "、".join(absent[:6]) + "」，你简历里都没出现。"
            "真做过就用 JD 的原词写出来；没做过别硬写——面试一问就崩")

    return {
        "mode": "rules",
        "verdict": (f"这版对这段 JD 是 {gap(profile, jd_text)['score']} 分。"
                    if (jd_text or "").strip() else "这版还没绑 JD。"),
        "moves": moves,
        "notClaimable": not_claimable,
        "opening": "",
        "uncertain": ["规则版只能指出方向，具体措辞建议你自己再落一遍；"
                      "接上大模型后会给出可直接采用的句子。"],
    }
