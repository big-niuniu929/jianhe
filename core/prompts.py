# -*- coding: utf-8 -*-
"""
内置提示词与输出模板 —— 接 DeepSeek 时直接生效
================================================
这个文件存在的意义：**提示词是产品资产，不是随手写的字符串。**

三条设计原则（每一条都来自这个产品要解决的具体问题）：

1. **输出必须是结构化 JSON，字段固定。**
   因为前端只有一套渲染逻辑。如果模型自由发挥，前端就要写十种兜底，
   而兜底本身会变成 bug 来源。宁可字段少，不可字段飘。

2. **显式禁止编造，并要求标注不确定。**
   落笔的立身之本是「反糊弄」。如果模型自己开始编数字、编公司、编薪资，
   这个产品就自相矛盾了，而且比不用 AI 更危险——用户会信。
   所以每个提示词里都有硬约束 + `_uncertain` 字段用来装"我不确定的部分"。

3. **不追求文采，追求可被追问。**
   所有 prompt 都要求"给出能被面试官继续追问的具体细节"，
   而不是"写得漂亮"。这是落笔和市面 AI 简历工具的分水岭。

成本控制：
- 岗位分析这类任务的输入主要是 JD + 简历片段，输出是结构化短文，
  单次约 2k 输入 / 1.8k 输出。按 DeepSeek 公开价约 0.02 元量级。
- 因此一律 `temperature` 低（0.2–0.4），减少重试，重试才是成本大头。
"""
import json
import re

# ================================================================
# 一、岗位行业分析
# ================================================================

SYS_INDUSTRY = """你是落笔的岗位分析师。你的读者是中国的大学生，他们正在准备实习或校招，
对目标岗位几乎一无所知，而且常常需要在几天内"看起来像个内行"。

你的任务不是写行业报告，而是让这个学生在面试里**不被问穿**。所以：

【硬约束】
1. 只输出 JSON，不要任何解释文字、不要 markdown 代码块标记。
2. 不要编造具体数字：不要写"该行业年增长率 12%"这类无法核实的数据。
   确实需要量级描述时，用"中小规模""百万级"这类区间词，并放进 _uncertain。
3. 不要编造具体公司、薪资、招聘量。
4. 不要写"综上所述""赋能""抓手"这类空话。每句话都要能被追问。
5. 词汇解释必须给出「为什么这么叫」，不能只是同义替换。
   反例：把"私域"解释成"自己的流量池"——这是同义替换，没有解释。
   正例：说清它相对于"公域"存在，载体是企微/社群，为什么企业要绕开平台。
6. 每个岗位族给 4 个细节题，必须是「真做过的人能秒答、没做过的人必卡壳」的类型。
   反例："你做过私域运营吗"（谁都能答做过）
   正例："你的社群是企业微信还是个人微信？多少个群？日活大概多少？"
7. 你不确定的地方，写进 _uncertain 数组，用一句话说明原因。
   宁可承认不确定，也不要给一个听起来对但实际是编的答案。

【语气】
克制、具体、像一个带过实习生的人。不用感叹号，不吹捧用户。"""


INDUSTRY_SCHEMA = {
    "familyLabel": "岗位族名称，如：电商运营 / 店铺运营",
    "confidence": 0.0,
    "oneLiner": "一句话定位，说清这个岗位对什么数字负责",
    "reportTo": "汇报对象",
    "kpi": ["考核指标，3–5 个"],
    "rhythm": [{"when": "日", "what": "这一天典型在做什么"}],
    "industry": {
        "上游": ["给这个岗位提供输入的角色/公司，2–4 个，每个说明给什么"],
        "自身": "这个岗位在链路中把什么加工成什么",
        "下游": ["拿走产出的角色，2–4 个"],
        "position": "这个位置在行业里的处境：议价权在哪、最容易被什么卡住",
    },
    "org": {
        "上游给你": ["组织内部，谁给你需求/资源"],
        "你交给谁": ["组织内部，谁用你的产出"],
        "平级协作": ["经常一起干活的角色"],
        "现实": "这个岗位在组织里的真实处境，一句不客气的话",
    },
    "vocab": [{
        "w": "术语",
        "def": "准确释义",
        "why": "为什么这么叫 / 它相对于什么存在",
        "use": "面试里怎么用这句话是加分",
        "trap": "用错会怎样，具体到会被问什么",
        "level": "基础 | 进阶 | 高门槛",
    }],
    "levels": [{"tier": "能沾手", "ok": "可以这么写", "notOk": "不要这么写"}],
    "probes": [{"q": "细节题", "expect": "什么样的回答算过关", "why": "面试官真正在验什么"}],
    "tools": ["这个岗位人手上真会开的软件/后台"],
    "_uncertain": ["你不确定或需要用户核实的地方"],
}


def build_industry(jd="", role="", resume_hint=""):
    """岗位分析提示词。jd 可为空——只有岗位名也能出，只是细节少一些。"""
    parts = [
        "请分析下面这个岗位，按指定 JSON 结构输出。",
        "",
        f"【岗位名称】{role or '（未明确，请从 JD 中推断）'}",
        "",
        "【JD 原文】",
        (jd or "（无 JD，请按岗位名称的通行情况分析，并在 _uncertain 中说明依据有限）")[:3000],
    ]
    if resume_hint:
        parts += [
            "",
            "【候选人已有经历摘要】（用于判断该重点讲哪些词）",
            resume_hint[:800],
        ]
    parts += [
        "",
        "【输出结构】严格按此 JSON，字段名不可改，不可增删顶层字段：",
        json.dumps(INDUSTRY_SCHEMA, ensure_ascii=False, indent=2),
        "",
        "【数量要求】vocab 不少于 15 个，probes 正好 4 个，kpi 3–5 个，levels 正好 3 条。",
        "vocab 按「JD 里出现过的」优先排序。",
        "",
        "只输出 JSON。",
    ]
    return "\n".join(parts)


def build_vocab_lookup(words=None, role=""):
    """只解释词汇的轻量提示词——用户粘贴 JD、想快速搞懂不认识的词时用。"""
    wl = words or []
    return "\n".join([
        "下面是用户在 JD 里遇到、但看不懂的专业词。逐个解释，输出 JSON 数组。",
        "",
        f"【岗位】{role or '未指定'}",
        f"【词表】{'、'.join(wl[:30])}",
        "",
        "每个对象字段：",
        '{"w":"词","def":"准确释义","why":"为什么这么叫","use":"面试怎么用是加分",'
        '"trap":"用错会怎样","level":"基础|进阶|高门槛"}',
        "",
        "要求：解释必须让一个完全没接触过的人听懂；不要同义替换。",
        "如果某个词你无法确定含义，把 def 写成「不确定：<你的猜测>」，不要编。",
        "只输出 JSON 数组。",
    ])


# ================================================================
# 二、实习坑库
# ================================================================

SYS_PITFALL = """你是落笔的实习风险顾问。读者是即将或正在实习的中国大学生。

【硬约束】
1. 只输出 JSON。
2. **不编造具体公司的负面信息**。不要点名任何公司。
3. **不给法律意见**，也不编造法条编号。涉及法律的只说"这类做法通常不被允许，建议咨询学校法律援助"。
4. 每条坑必须包含四段，缺一段就不算完整：
   - scene：具体到什么场景、什么话（要说人话，像同事私下告诉你）
   - signal：早期的可观察信号（学生自己能察觉的那种）
   - avoid：事前怎么避，要可执行
   - fix：已经踩了怎么救，要可执行
5. 严重性分「高/中/低」，判断标准是"对学生实际影响程度"，不是法律定性。
6. 不要写"要谨慎""要注意沟通"这种没有动作的建议。
   反例："建议与领导保持良好沟通"
   正例："入职第二周要一件有交付物的事，判断标准是：做完你能说清给谁用、解决了什么"
7. 每个岗位给 5–7 条，其中至少 2 条是这个岗位**特有**的，不能是"加班""沟通"这种通用内容。"""


PITFALL_SCHEMA = [{
    "id": "短英文 id",
    "stage": "find | onboard | work | convert | leave",
    "severity": "高 | 中 | 低",
    "title": "一句话说清这个坑是什么",
    "scene": "具体场景，用'你'和直接引语，2–4 句",
    "signal": "早期信号，1–2 句",
    "avoid": "事前怎么避，含判断标准",
    "fix": "踩了怎么救",
    "write": "这段经历在简历里怎么写才不丢人",
}]


def build_pitfalls(role="", industry_hint=""):
    return "\n".join([
        "请列出下面这个岗位的实习生最常踩的坑，按指定 JSON 数组输出。",
        "",
        f"【岗位】{role}",
        f"【行业/公司类型】{industry_hint or '未指定，按通行情况'}",
        "",
        "【输出结构】JSON 数组，每项字段严格如下：",
        json.dumps(PITFALL_SCHEMA, ensure_ascii=False, indent=2),
        "",
        "【数量】5–7 条。至少 2 条必须是这个岗位特有的（比如电商的刷单、HR 的简历隐私、"
        "销售的垫付招待费），不要用「加班多」「沟通难」凑数。",
        "",
        "只输出 JSON 数组。",
    ])


# ================================================================
# 二·五、一岗一版：按 JD 定制
# ================================================================

SYS_TAILOR = """你是落笔的简历对齐顾问。用户正在为**某一个具体岗位**准备一版简历，
他已经有一份通用简历，现在要花最少的时间把这版调到能投。

【你要判断的事】
不是"这份简历好不好"，而是"**投这一家，哪几句会成为把柄**"。
通用简历的问题在每家公司身上表现不同：投运营，缺的是数据口径；
投产品，缺的是需求决策的边界。你要指出这一家的问题。

【硬约束】
1. 只输出 JSON，不要解释文字、不要代码块标记。
2. **绝对不能新增事实**：不能新增数字、公司、时间、职级、职责。
   原文没有的数字一律写「（待补）」占位，不许自己造一个合理的。
3. 每条 move 必须给出 bid —— 就是输入里那句经历前面的编号，不能自己编编号。
4. covers 只能从「JD 缺词」列表里挑，且改后的句子必须真的出现了那个词。
   改后句子里没有的词，不要写进 covers。这是最容易露馅的地方。
5. notClaimable 是**最重要的字段**：JD 要求但用户确实没做过的东西，
   明确告诉他"别写"。宁可少一个关键词，也不要让他面试时崩在第一轮。
6. 不做文采修饰。改完的句子要能在面试里被追问三层而不塌。
7. verdict 一句话，说清"这版投出去最可能死在哪"。

【语气】像一个带过实习生、说话不好听但讲道理的师兄。不吹捧，不用感叹号。"""

TAILOR_SCHEMA = {
    "verdict": "一句话：这版投这一家最可能卡在哪",
    "moves": [{
        "bid": "经历条目的编号，如 internships.0.1",
        "before": "原文（照抄，不要改）",
        "after": "改后（≤80 字，强动词开头，不新增事实）",
        "covers": ["改后覆盖到的 JD 缺词，只能来自缺词列表"],
        "why": "为什么这么改，一句说清",
        "verify": "采用前必须自己确认的一件事（数字口径/边界/是否真做过）",
    }],
    "notClaimable": ["JD 要求但用户没做过、不许硬写的东西，每条说明原因"],
    "opening": "面试自我介绍第一句，针对这个岗位，≤40 字",
    "_uncertain": ["不确定、需要用户核实的地方"],
}


def build_tailor(role="", company="", jd="", bullets=None, missing=None):
    """
    bullets: [{"bid": "...", "text": "..."}]  —— 必须是带编号的，否则模型会瞎编 bid
    missing: JD 里简历没覆盖的关键词，用于约束 covers
    """
    bs = bullets or []
    lines = "\n".join(f"{b['bid']} | {b['text'][:180]}" for b in bs[:26])
    return "\n".join([
        "下面这份简历要投某个具体岗位。请按指定 JSON 结构给出对齐方案。",
        "",
        f"【目标岗位】{role or '（见 JD）'}",
        f"【公司】{company or '（未提供）'}",
        "",
        "【JD 原文】",
        (jd or "（无 JD）")[:2600],
        "",
        "【JD 里简历没覆盖到的关键词】",
        "、".join((missing or [])[:14]) or "（无，关键词都覆盖了）",
        "",
        "【简历里的经历条目】格式：编号 | 原文",
        lines or "（空）",
        "",
        "【输出结构】严格按此 JSON，字段名不可改：",
        json.dumps(TAILOR_SCHEMA, ensure_ascii=False, indent=2),
        "",
        "【数量】moves 最多 4 条，只挑最值得动的；宁可 2 条，不要凑数。",
        "notClaimable 至少 1 条（如果 JD 里的东西他都做过，就写「暂无明显缺口」并说明依据）。",
        "",
        "只输出 JSON。",
    ])


def coerce_tailor(obj, valid_bids=None, missing_words=None):
    """
    收敛模型输出。
    这里做两件"不信任模型"的事：
      1. bid 不在原文编号里 → 直接丢掉（否则前端会改到另一句话上）
      2. covers 里出现缺词列表之外的词 → 剔除（它只是想显得覆盖了）
    数字防新增交给 rewrite.fact_guard 在调用侧做，这里只保证结构。
    """
    from . import rewrite as _rw

    if not isinstance(obj, dict):
        raise ValueError("对齐方案不是对象")
    valid = set(valid_bids or [])
    # 大小写映射：模型常把 sql 写成 SQL，不能因此判成"越界"
    miss_map = {w.lower(): w for w in (missing_words or [])}

    moves = []
    for m in (obj.get("moves") or [])[:5]:
        if not isinstance(m, dict):
            continue
        bid = _s(m.get("bid"), 40)
        after = _s(m.get("after"), 400)
        if not after or (valid and bid not in valid):
            continue
        covers = []
        for w in _sl(m.get("covers"), 6, 20):
            canon = miss_map.get(w.lower())
            if canon is None:
                if not miss_map:
                    covers.append(w)
                continue          # 缺词列表里没有 → 它只是想显得覆盖了
            if canon.lower() in after.lower() and canon not in covers:
                covers.append(canon)
        g = _rw.fact_guard(_s(m.get("before"), 400), after)
        moves.append({
            "bid": bid,
            "before": _s(m.get("before"), 400),
            "after": after,
            "covers": covers,
            "why": _s(m.get("why"), 300),
            "verify": _s(m.get("verify"), 300),
            "guard": g,
        })

    return {
        "mode": "llm",
        "verdict": _s(obj.get("verdict"), 200),
        "moves": moves,
        "notClaimable": _sl(obj.get("notClaimable") or obj.get("not_claimable"), 6, 200),
        "opening": _s(obj.get("opening"), 120),
        "uncertain": _sl(obj.get("_uncertain") or obj.get("uncertain"), 5, 160),
    }


# ================================================================
# 三、输出解析与校验（模型一定会加 markdown 围栏，必须兜住）
# ================================================================

def extract_json(text):
    """
    从模型输出里抽出 JSON。
    三级兜底：直接解析 → 去围栏 → 括号配对扫描。
    最后一级是必须的：DeepSeek 偶尔会在 JSON 前后加一句"好的，以下是..."
    """
    if not text:
        raise ValueError("空输出")
    t = text.strip()

    try:
        return json.loads(t)
    except Exception:
        pass

    # 去掉 ```json ... ```
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except Exception:
            t = m.group(1).strip()

    # 括号配对扫描，找第一个完整的 {...} 或 [...]
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start = t.find(open_c)
        if start < 0:
            continue
        depth, in_str, esc = 0, False, False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == open_c:
                depth += 1
            elif ch == close_c:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except Exception:
                        break
    raise ValueError("模型输出不是合法 JSON")


def _s(v, n=600):
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        v = "；".join(str(x) for x in v if x)
    return str(v).strip()[:n]


def _sl(v, n=12, each=300):
    if not v:
        return []
    if isinstance(v, str):
        v = [x for x in re.split(r"[\n;；]", v) if x.strip()]
    return [_s(x, each) for x in list(v)[:n] if _s(x, each)]


def coerce_industry(obj):
    """
    把模型输出强制收敛到前端认识的结构。
    模型漏字段、给错类型是常态，这里必须容错——否则前端空白，
    用户看到的是"分析失败"，而我们其实拿到了一份 90% 可用的内容。
    """
    if not isinstance(obj, dict):
        raise ValueError("岗位分析结果不是对象")

    ind = obj.get("industry") or {}
    org = obj.get("org") or {}

    vocab = []
    for v in (obj.get("vocab") or [])[:40]:
        if isinstance(v, str):
            vocab.append({"w": v, "def": "", "why": "", "use": "", "trap": "", "level": "基础", "inJd": False})
        elif isinstance(v, dict):
            vocab.append({
                "w": _s(v.get("w") or v.get("word") or v.get("term"), 40),
                "def": _s(v.get("def") or v.get("definition")),
                "why": _s(v.get("why")),
                "use": _s(v.get("use")),
                "trap": _s(v.get("trap")),
                "level": _s(v.get("level"), 8) or "基础",
                "inJd": bool(v.get("inJd")),
            })
    vocab = [v for v in vocab if v["w"]]

    probes = []
    for p in (obj.get("probes") or [])[:6]:
        if isinstance(p, dict):
            probes.append({
                "q": _s(p.get("q") or p.get("question")),
                "expect": _s(p.get("expect") or p.get("good")),
                "why": _s(p.get("why")),
            })
        elif isinstance(p, str):
            probes.append({"q": _s(p, 200), "expect": "", "why": ""})
    probes = [p for p in probes if p["q"]]

    levels = []
    for l in (obj.get("levels") or [])[:3]:
        if isinstance(l, dict):
            levels.append({
                "tier": _s(l.get("tier") or l.get("name"), 40),
                "ok": _s(l.get("ok") or l.get("can")),
                "notOk": _s(l.get("notOk") or l.get("cannot")),
            })
    if not levels:
        levels = [{"tier": "能沾手", "ok": "", "notOk": ""}]

    rhythm = []
    for r in (obj.get("rhythm") or [])[:6]:
        if isinstance(r, dict):
            rhythm.append({"when": _s(r.get("when"), 8), "what": _s(r.get("what"))})
        elif isinstance(r, str):
            rhythm.append({"when": "", "what": _s(r)})

    return {
        "ok": True,
        "family": obj.get("family") or "generic",
        "familyLabel": _s(obj.get("familyLabel") or obj.get("label"), 40) or "岗位",
        "confidence": float(obj.get("confidence") or 0.6),
        "matchedBy": [],
        "oneLiner": _s(obj.get("oneLiner") or obj.get("one_liner")),
        "reportTo": _s(obj.get("reportTo"), 120),
        "kpi": _sl(obj.get("kpi"), 8, 60),
        "rhythm": rhythm,
        "industry": {
            "上游": _sl(ind.get("上游") or ind.get("upstream"), 6),
            "自身": _s(ind.get("自身") or ind.get("self")),
            "下游": _sl(ind.get("下游") or ind.get("downstream"), 6),
            "position": _s(ind.get("position")),
        },
        "org": {
            "上游给你": _sl(org.get("上游给你") or org.get("upstream"), 6),
            "你交给谁": _sl(org.get("你交给谁") or org.get("downstream"), 6),
            "平级协作": _sl(org.get("平级协作") or org.get("peers"), 8),
            "现实": _s(org.get("现实") or org.get("reality")),
        },
        "vocab": vocab,
        "levels": levels,
        "probes": probes,
        "tools": _sl(obj.get("tools"), 10, 40),
        "jdVocab": [],
        "mode": "llm",
        "source": "DeepSeek 生成的岗位分析（已按内置模板校验）",
        "uncertain": _sl(obj.get("_uncertain") or obj.get("uncertain"), 6, 200),
    }


def coerce_pitfalls(obj, family=""):
    if isinstance(obj, dict):
        obj = obj.get("items") or obj.get("data") or obj.get("pitfalls") or []
    if not isinstance(obj, list):
        raise ValueError("坑库结果不是数组")

    out = []
    for i, x in enumerate(obj[:10]):
        if not isinstance(x, dict):
            continue
        title = _s(x.get("title"), 80)
        if not title:
            continue
        stage = _s(x.get("stage"), 12)
        if stage not in ("find", "onboard", "work", "convert", "leave"):
            stage = "work"
        sev = _s(x.get("severity"), 4)
        if sev not in ("高", "中", "低"):
            sev = "中"
        out.append({
            "id": _s(x.get("id"), 12) or f"ai{i}",
            "stage": stage,
            "severity": sev,
            "title": title,
            "scene": _s(x.get("scene")),
            "signal": _s(x.get("signal")),
            "avoid": _s(x.get("avoid")),
            "fix": _s(x.get("fix")),
            "write": _s(x.get("write")),
            "scope": "AI 补充（针对你的岗位生成）",
            "family": family,
        })
    return out


MODE_NOTES = {
    "llm": "已用大模型生成，内容按内置模板校验过。仍建议对照你的真实经历核对。",
    "rules": "当前是内置知识库版本（不依赖网络）。接上大模型后会针对你的具体 JD 生成。",
    "fallback": "大模型调用失败，已自动降级到内置知识库。功能没有中断。",
}


# ================================================================
# 社区帖子的 AI 总结
# ----------------------------------------------------------------
# 这个功能的定位和别处的 AI 不一样：它不是"帮用户写"，是**帮下一个读者省时间**。
# 所以三条硬要求：
#   1. 只能总结帖子里写了的东西，不许补常识、不许扩写。
#      一条帖子如果有用，价值在于"这一件事真的发生过"；模型一扩写就变成通稿了。
#   2. 必须指出这条帖子的**证据强度**。「我被扣了押金」和「听说有人被扣押金」
#      不是一回事，读者要能看出区别。
#   3. 必须给"如果你是这个人，下一步做什么"，否则总结只是复述。
# ----------------------------------------------------------------
POST_SUMMARY_SCHEMA = {
    "headline": "一句话说清这条帖子最值得记住的事，20 字内",
    "kind": "坑 / 经验 / 求助 / 吐槽（四选一）",
    "signals": ["帖子里提到的早期信号，最多 3 条，只写帖子真说了的"],
    "avoid": ["从这条帖子能拿走的做法，最多 3 条，动词开头"],
    "evidence": "证据强度：亲历 / 转述 / 情绪表达 —— 并说明凭什么这么判",
    "whoFits": "这条对什么处境的人有用，一句话",
    "missing": ["帖子里没说清、读者需要自己确认的点，最多 2 条"],
}


def build_post_summary(title="", body="", role="", stage=""):
    """社区帖子的总结提示词。刻意把帖子原文整段喂进去，不做截断以外的加工。"""
    return "\n".join([
        "下面是一条求职 / 实习社区里的帖子。请为「还没读这条帖子的人」做一份总结。",
        "",
        "【岗位】" + (role or "（未填）"),
        "【阶段】" + (stage or "（未填）"),
        "【标题】" + (title or ""),
        "【正文】",
        (body or "")[:4000],
        "",
        "【硬约束】",
        "1. 只允许出现帖子里写过的内容。不要补充行业常识、不要举例、不要替作者完善。",
        "2. 帖子里没说的，一律进 missing，不要猜。",
        "3. 如果帖子信息量很少或主要是情绪，headline 就直说「信息量有限」，",
        "   signals / avoid 可以为空数组，不要为了填满字段而编。",
        "4. evidence 必须判断这条是亲历、转述还是纯情绪，并给出依据（比如「用了『我』」）。",
        "",
        "【输出结构】严格按此 JSON：",
        json.dumps(POST_SUMMARY_SCHEMA, ensure_ascii=False, indent=2),
        "",
        "只输出 JSON。",
    ])


def coerce_post_summary(obj):
    """把总结收敛到前端认识的结构。字段缺失不报错——宁可少显示，不要空白。"""
    if not isinstance(obj, dict):
        raise ValueError("总结结果不是对象")
    return {
        "headline": _s(obj.get("headline"), 80) or "（模型没给出要点）",
        "kind": _s(obj.get("kind"), 6) or "经验",
        "signals": _sl(obj.get("signals"), 3, 120),
        "avoid": _sl(obj.get("avoid"), 3, 120),
        "evidence": _s(obj.get("evidence"), 160),
        "whoFits": _s(obj.get("whoFits"), 120),
        "missing": _sl(obj.get("missing"), 2, 120),
    }
