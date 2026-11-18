# -*- coding: utf-8 -*-
"""
岗位地图的「地图外补齐」
=======================
用户需求原话：「先内置一套基础的岗位信息，如果用户输入了地图之外的岗位，
则调用大模型去填补。」

所以这里只做一件事：**把 detect() 认不出的岗位，用模型补成一个和内置族同构的族，
挂进内存，并落盘缓存。**

三个设计决定，每个都在防一种具体的坏结果：

1. **补齐结果是「族」，不是「这个 JD 的分析」。**
   如果只对当前 JD 生成一份分析，那下一个用户问同一个岗位要再烧一次 token，
   而且两人看到的说法不一致。补成族 + 缓存，一次成本、全站复用、
   且后续 pitfalls / audit 也能认识这个岗位。

2. **缓存键走归一化，不走原始输入。**
   「临床研究」「临床研究员 」「临床研究（实习）」必须是同一个键，
   否则缓存命中率会低到没有意义。归一化见 norm_key()。

3. **补齐不扣点，但要登录 + 每日限额；用户自带 Key 则不限。**
   这是"公共品"型调用：结果全站共享，按次扣点等于让第一个吃螃蟹的人
   替所有人买单，用户就不敢用免费功能了。但完全不设限会被刷。
   折中是「登录 + 每天 12 次」，正常用户一天根本问不到 12 个新岗位。
"""
import json
import re
import time

from . import store
from . import industry as industry_mod
from . import llm as llm_mod
from . import prompts as prompts_mod

# 每个账号每天能触发的平台补全次数。超过了就只读缓存，不再调模型。
DAILY_LIMIT = 12

# 归一化时要去掉的噪声词：年份、学历、招聘类型，它们不影响「这是什么岗位」
_NOISE = re.compile(
    r"20\d{2}|19\d{2}|实习生|实习|应届|校招|社招|兼职|全职|远程|急招|招聘|岗位|"
    r"本科|硕士|博士|大专|不限|届")
_PUNCT = re.compile(r"[\s\-_/\\|,，、。·:：()（）\[\]【】<>《》\"'’“”!！?？+*&~`]+")
# 「临床研究员」和「临床研究」是同一个岗位，「会计师」和「会计」也是。
# 但只在剥掉之后还剩 3 个字以上才剥——否则「设计师」会被剥成「设计」，
# 和内置的 design 族语义打架。
_SUFFIX = ("员", "师", "工")


def base_name(role):
    """
    去掉年份/实习/学历这些修饰，留下岗位本名：「2025届临床研究（实习）」→「临床研究」。
    为什么要它：detect() 是子串匹配（alias in text），
    如果只把用户原话「临床研究（实习）」挂成别名，
    下一个人输入「临床研究」反而认不出来——这是他最常输入的形式。
    """
    s = (role or "").strip()
    s = _PUNCT.sub(" ", s)
    s = _NOISE.sub(" ", s)
    s = re.sub(r"\s+", "", s)
    if len(s) >= 4 and s.endswith(_SUFFIX):
        s = s[:-1]
    return s[:24]


def norm_key(role):
    """岗位名 → 缓存键。同一岗位的不同写法必须撞到同一个键。"""
    b = base_name(role)
    return ("x_" + b) if b else ""


def _family_from_analysis(res, role):
    """
    把 coerce_industry() 的输出（前端用的结构）翻译成内置族的结构（知识层用的结构）。
    两套结构不一致是历史原因：内置族是手写的，字段是元组以省体积；
    模型输出走 JSON，只能是 dict。翻译集中在这一处，别处不要再做第二次。
    """
    rhythm = {}
    for r in res.get("rhythm") or []:
        when = (r.get("when") or "").strip()
        what = (r.get("what") or "").strip()
        if what:
            rhythm[when or "日常"] = what

    vocab = []
    for v in res.get("vocab") or []:
        if v.get("w"):
            vocab.append((v["w"], v.get("def", ""), v.get("why", ""),
                          v.get("use", ""), v.get("trap", "")))

    levels = [(l["tier"], l.get("ok", ""), l.get("notOk", ""))
              for l in res.get("levels") or [] if l.get("tier")]
    if not levels:
        levels = [("能沾手", "", "")]

    probes = [(p["q"], p.get("expect", ""), p.get("why", ""))
              for p in res.get("probes") or [] if p.get("q")]

    label = res.get("familyLabel") or role or "岗位"
    # 别名顺序 = 识别权重。本名放第一位（detect 对首位别名加权 2 倍），
    # 因为它就是这个族存在的理由；模型给的长名放最后，只做补充。
    aliases = []
    for a in [base_name(role), (label or "").strip(), (role or "").strip()]:
        if a and a not in aliases and 2 <= len(a) <= 20:
            aliases.append(a)
    if not aliases:
        aliases = [(role or "岗位").strip()[:20]]

    return {
        "label": label,
        "aliases": aliases,
        "one_liner": res.get("oneLiner") or "",
        "report_to": res.get("reportTo") or "",
        "kpi": res.get("kpi") or [],
        "rhythm": rhythm,
        "industry": res.get("industry") or {},
        "org": res.get("org") or {},
        "vocab": vocab,
        "levels": levels,
        "probes": probes,
        "tools": res.get("tools") or [],
        # 模型自己标的不确定项，跟着族一起存——UI 上有地方要显示它
        "_uncertain": res.get("uncertain") or [],
    }


def from_cache(role):
    """
    只查缓存，不调模型。给 /api/industry 的同步路径用：
    命中就直接返回族，命中不了返回 None，页面照样秒开。
    """
    key = norm_key(role)
    if not key:
        return None
    row = store.jobmap_get(key)
    if not row:
        return None
    try:
        data = json.loads(row["data"])
    except Exception:
        return None
    fam = _family_from_analysis(data, row["role"] or role)
    industry_mod.register(key, fam)
    return key, fam


def _today_count(owner):
    return store.count_events(owner, "jobmap_fill", since=_day_start())


def _day_start():
    t = time.localtime()
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))


def fill(role, jd="", owner=None, need_login=None):
    """
    真正去补一个族。返回 dict：
        {"ok": True,  "key": ..., "family": ..., "cached": False, "source": "user"/"platform"}
        {"ok": False, "reason": "nocfg"/"needlogin"/"quota"/"bad"/"error", "hint": "给人看的一句话"}

    调用方（server 的 /api/industry/fill）负责把这些 reason 翻成前端能显示的提示。
    这里不 import server，也不发 HTTP，保持可单测。
    """
    key = norm_key(role)
    if not key or len(key) <= 3:
        return {"ok": False, "reason": "bad", "hint": "岗位名太短，多写两个字再试"}

    hit = from_cache(role)
    if hit:
        return {"ok": True, "key": hit[0], "family": hit[1], "cached": True, "source": "cache"}

    cfg, source = llm_mod.cfg_for(owner)
    if not cfg:
        return {"ok": False, "reason": "nocfg",
                "hint": "还没有可用的模型。可以在「模型接入」里填自己的 Key，或用平台额度。"}

    if source == "platform":
        if need_login:
            return {"ok": False, "reason": "needlogin",
                    "hint": "地图外岗位要调模型补齐，登录后可用平台的额度（每天 12 个，不扣点）。"}
        if _today_count(owner) >= DAILY_LIMIT:
            return {"ok": False, "reason": "quota",
                    "hint": "今天的自动补全额度用完了（每天 %d 个）。可以填自己的 Key，不限次。" % DAILY_LIMIT}

    prompt = prompts_mod.build_industry(jd=jd, role=role)
    try:
        content, usage = llm_mod.chat_usage(prompt, cfg=cfg)
    except Exception as e:
        return {"ok": False, "reason": "error",
                "hint": "模型这次没成功（%s）。内置地图照常用，不影响其他功能。" % _short(e)}

    try:
        obj = prompts_mod.extract_json(content)
        res = prompts_mod.coerce_industry(obj)
    except Exception as e:
        return {"ok": False, "reason": "bad",
                "hint": "模型返回的内容没法解析（%s）。换一次再试。" % _short(e)}

    if not res.get("vocab") or len(res["vocab"]) < 5:
        return {"ok": False, "reason": "bad",
                "hint": "模型这次给的内容太少，没有采纳。稍后再试一次。"}

    fam = _family_from_analysis(res, role)
    industry_mod.register(key, fam)
    store.jobmap_put(key, role, json.dumps(res, ensure_ascii=False), source=source)
    if source == "platform":
        store.log(owner, "jobmap_fill", {"role": role, "key": key,
                                         "tokens": usage.get("total_tokens"),
                                         "cached": False})
    return {"ok": True, "key": key, "family": fam, "cached": False,
            "source": source, "tokens": usage.get("total_tokens"),
            "uncertain": fam.get("_uncertain") or []}


def _short(e):
    s = str(e).replace("\n", " ")
    return s[:60]


def stats():
    """运营视角：哪些地图外岗位被问得最多——这些该补进内置库了。"""
    rows = store.jobmap_top(30)
    return {
        "cached": len(rows),
        "top": [{"role": r["role"], "hits": r["hits"], "source": r["source"]} for r in rows],
    }
