# -*- coding: utf-8 -*-
"""
drill —— 面试陪练的「答完之后」
================================
core/interview.py 解决的是「该准备哪些问题」，
这个模块解决的是「我这么答，会被怎么追」。

设计取舍（很重要，别改）
------------------------
1. **不给综合评分。** 「82 分」会让人去追分，而不是去补洞。
   这里给四个能打勾的检查点，加上「面试官下一句会问什么」。
   分数是评价，检查点是动作——用户要的是动作。

2. **追问必须针对这一答里真实缺的东西。** 所以每个维度先抓证据
   （有没有数字、有没有「我」、有没有前后对比），抓不到才生成那句追问，
   并把**抓到的原文片段**带进追问里。用户看到「你提到的『3 倍』是怎么统计的」
   才知道系统真的读了他的答案，而不是在套模板。

3. **纯规则、零成本、永久免费。** 写答案是高频动作，高频动作不能收费，
   也不能挂在登录后面。这是产品的底线。

4. 规则一定会误判。所以每个检查点都返回 `hit`（凭什么这么判），
   用户能一眼看出「它抓错了」，而不是被一个黑箱判低分。
"""
import re

# ---------------------------------------------------------------- 词表
# 数字 + 单位。单位是判断「这个数字有没有业务含义」的关键：
# 「做了 3 个」和「提升 3 倍」在面试官眼里不是一回事，但对规则来说都是数字，
# 所以单位要单独抓出来，好让追问能说到点上。
_NUM = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|％|倍|万|千|个|份|名|人|次|条|台|套|"
    r"元|块|页|篇|家|单|场|期|轮|款|张|段|公里)"
    r"|[一二三四五六七八九十两]+\s*(?:成|倍)")
# 时间型数字（11 月、2025 年、第 3 天）不是「量化产出」，要先从文本里剥掉，
# 否则「11月我负责…」会被判成「有数字」，白白放过一条真正缺失的检查点。
_DATE_LIKE = re.compile(
    r"20\d{2}\s*年(?:\s*\d{1,2}\s*月)?|\d{1,2}\s*月(?:\s*\d{1,2}\s*[日号])?"
    r"|\d{1,2}\s*[日号]|第[一二三四五六七八九十\d]+天")

# 结果 / 变化。没有这些词，说明只讲了「我做了什么」，没讲「所以怎样了」。
_RESULT = (
    "提升", "提高", "增长", "上涨", "下降", "降低", "减少", "缩短", "节省",
    "翻倍", "翻了一", "变成", "达到", "达成", "超额", "排名", "拿到", "转正",
    "占比", "转化率", "留存", "复购", "成本", "降到", "做到", "第一次",
)
# 「从 X 到 Y」是中文里最常见的对比句式，比上面任何单个词都可靠。
_FROM_TO = re.compile(r"从.{0,14}?(?:到|至|变成|降|升)")

# 具体性：时间锚点。有它才叫「一件事」，没它叫「一类事」。
_TIME = (
    "月", "周", "学期", "暑假", "寒假", "实习期间", "大促", "双11", "双十一",
    "618", "开学", "期末", "季度", "Q1", "Q2", "Q3", "Q4", "去年", "今年",
    "上个月", "那周", "当天", "第一天", "大三", "大四", "研一", "研二",
)
# 具体性：可指认的对象。机构、平台、项目名、带引号的东西。
_OBJECT = re.compile(r"「[^」]{2,20}」|《[^》]{2,30}》|[A-Za-z]{3,}|公司|学校|学院|部门|社团|实验室|平台|店铺|门店|项目组")

# 含糊词：出现这些，基本等于没答。
_VAGUE = (
    "差不多", "大概", "还行", "挺好的", "没什么", "不知道", "不清楚", "不太会",
    "一些", "有点", "尽量", "想办法", "努力", "认真", "积极", "学习能力强",
    "沟通能力强", "都可以", "随便",
)
# 实质动作词：有它们说明答的是「做了什么」，不是「我人怎么样」。
_ACTION = (
    "负责", "主导", "牵头", "搭建", "设计", "实现", "优化", "排查", "解决",
    "整理", "复盘", "对接", "推进", "落地", "写", "做", "改", "谈", "跑",
    "统计", "分析", "上线", "交付", "提案", "验证",
)

_FIRST = ("我",)
_WE = ("我们", "咱们", "团队", "大家")

# 四个检查点。key 用于前端做图标，label 是给人看的名字。
DIMS = [
    ("spec", "有具体的事", "哪个月、哪个项目、跟谁。没有锚点的经历听起来像编的。"),
    ("quant", "有能核查的数", "做了多少、快了多少、错了多少。数字是唯一不能含糊的东西。"),
    ("own", "分得清你我", "「我们」越多，面试官越会问「那你具体做了什么」。"),
    ("result", "有前后对比", "只讲动作不讲结果，等于告诉对方「我没看结果」。"),
]


def _chars(t):
    return len(re.sub(r"\s+", "", t or ""))


def _metric_text(t):
    """剥掉日期型数字后的正文。量化判断只在它上面做。"""
    return _DATE_LIKE.sub(" ", t or "")


def _first_num(text):
    m = _NUM.search(_metric_text(text))
    return m.group(0).strip() if m else ""


def _snippet(text, n=14):
    """截一段原文当证据。用在追问里，让用户知道系统在说哪一句。"""
    s = re.sub(r"\s+", " ", (text or "").strip())
    return s[:n] + ("…" if len(s) > n else "")


def _count(text, words):
    return sum(text.count(w) for w in words)


def analyze(answer, question=""):
    """审一答。返回四个检查点 + 面试官接下来会问什么 + 一句改法。"""
    t = (answer or "").strip()
    n = _chars(t)

    # ---------------------------------------------- 空答
    if n == 0:
        return {
            "chars": 0, "grade": "空", "gradeLevel": 0,
            "dims": [{"key": k, "label": lab, "score": 0, "ok": False,
                      "hit": "还没写"} for k, lab, why in DIMS],
            "asks": [], "fix": "先随便写三行也行——写下来才能看出漏在哪。",
            "best": "", "worst": "spec", "vague": [],
        }

    # ---------------------------------------------- 四个维度
    has_num = bool(_NUM.search(_metric_text(t)))
    num_txt = _first_num(t)
    has_result = any(w in t for w in _RESULT) or bool(_FROM_TO.search(t))
    has_time = any(w in t for w in _TIME)
    has_obj = bool(_OBJECT.search(t))
    n_me = _count(t, _FIRST)
    n_we = _count(t, _WE)
    acts = [w for w in _ACTION if w in t]
    vague = [w for w in _VAGUE if w in t]

    dims = []

    # 1. 具体 —— 锚点（时间 / 对象）+ 篇幅
    s = (12 if n >= 100 else 10 if n >= 60 else 6 if n >= 40 else 3 if n >= 20 else 0)
    s += 8 if has_obj else 0
    s += 5 if has_time else 0
    hit = []
    if has_time:
        hit.append("有明确时间")
    if has_obj:
        hit.append("有可指认的对象")
    dims.append({"key": "spec", "score": min(s, 25),
                 "hit": "；".join(hit) or "抓到 %d 字，但没有时间点，也没有具体项目名" % n,
                 "ok": s >= 18})

    # 2. 可核查 —— 数字
    s = 25 if has_num else 0
    dims.append({"key": "quant", "score": s,
                 "hit": ("抓到「%s」" % num_txt) if has_num else "整段没有任何数字",
                 "ok": has_num})

    # 3. 边界 —— 「我」vs「我们」
    if n_me == 0:
        s, h = 4, "通篇没有第一人称，看不出你做了什么"
    elif n_we and n_we >= n_me and n_we > 0:
        s, h = 10, "「我们/团队」出现 %d 次，「我」只有 %d 次" % (n_we, n_me)
    elif acts:
        s, h = 25, "以「我」为主，动作是：%s" % "、".join(acts[:3])
    else:
        s, h = 15, "有「我」，但看不出具体动作"
    dims.append({"key": "own", "score": s, "hit": h, "ok": s >= 18})

    # 4. 结果 —— 对比 / 变化
    if has_result:
        s, h = 25, "有前后对比"
    elif has_num:
        s, h = 12, "有数字，但没说这个数字和之前比变了多少"
    else:
        s, h = 4, "只讲了过程，没讲最后变成什么样"
    dims.append({"key": "result", "score": s, "hit": h, "ok": s >= 18})

    # ---------------------------------------------- 追问（按缺得最狠的排）
    asks = []
    for d in sorted(dims, key=lambda x: x["score"]):
        if d["ok"] or len(asks) >= 3:
            continue
        asks.append(_ask_for(d["key"], t, num_txt))

    # ---------------------------------------------- 定级
    # 刻意**不用总分**。加总会让「三条满分」掩掉「一条零分」，
    # 于是出现「四个点过了 3 个」却判「够用了」这种自相矛盾。
    # 面试官不是按总分决定要不要追，是按「还有哪个洞」。
    # 所以定级只看没过几条。
    best = max(dims, key=lambda x: x["score"])["key"]
    weak = [d for d in dims if not d["ok"]]
    worst = min(weak, key=lambda x: x["score"])["key"] if weak else ""

    nbad = len(weak)
    if nbad == 0:
        grade, lv = "够用了", 3
    elif nbad <= 2:
        grade, lv = "会被追问", 2
    elif n < 30:
        grade, lv = "基本没答", 1
    else:
        grade, lv = "会被打断", 1
    # 含糊词是硬伤：出现就压到「会被追问」以下
    if vague and lv > 2:
        grade, lv = "会被追问", 2

    return {
        "chars": n, "grade": grade, "gradeLevel": lv,
        "dims": dims, "asks": asks, "vague": vague,
        "best": best, "worst": worst, "nbad": nbad,
        "fix": _fix_for(worst, t, num_txt),
    }


def _ask_for(key, t, num_txt):
    """面试官下一句。必须带原文，否则用户觉得在套模板。"""
    snip = _snippet(t, 12)
    if key == "quant":
        if num_txt:
            return {"dim": "quant", "label": "数字口径",
                    "q": "你提到的「%s」是怎么统计的？统计周期多长、样本有多少？" % num_txt}
        return {"dim": "quant", "label": "缺数字",
                "q": "这件事有没有一个能说出来的数——做了多少、快了多少、错了几次？"}

    if key == "spec":
        return {"dim": "spec", "label": "缺场景",
                "q": "「%s」这段，是哪个月、在哪个项目上发生的？当时团队几个人？" % snip}

    if key == "own":
        return {"dim": "own", "label": "角色边界",
                "q": "这里哪些是你自己决定的、哪些是执行别人的决定？你一个人负责哪一块？"}

    if key == "result":
        return {"dim": "result", "label": "缺结果",
                "q": "做完之后变成什么样了？跟前一个版本比，差在哪？"}

    return {"dim": key, "label": "追问", "q": "能再具体讲一下这件事吗？"}


def _fix_for(worst, t, num_txt):
    """一句改法。只改一件事——同时给四条建议等于没给。"""
    if not worst:
        return "这四条都过了。剩下能做的只有一件事：把这段话脱稿说两遍，说到不打结。"
    if worst == "quant":
        return ("补一个能核查的数：做了多少条 / 花了多少天 / 涉及几个人 / 转化差了几个点。"
                "记不清精确值就写「约 30 条」，但一定要带单位——没有单位的数字等于没说。")
    if worst == "spec":
        return "开头补九个字：「（某年某月）在某项目上」。有了这个锚点，后面所有细节才立得住。"
    if worst == "own":
        return "把「我们」拆开：团队做了三件事，其中**你**负责哪一件、做到什么程度。"
    if worst == "result":
        return "结尾加一句「所以……」：改之前是多少、改之后是多少。过程再详细，没有这一句，对方会认为你没看结果。"
    return "按「场景 → 你的任务 → 你的动作 → 结果」四步重写一遍，每步一句话就够。"


# ---------------------------------------------------------------- 追问链
# 用户点开某条追问、写下新答案，再审一次——这就形成一条链。
# 链本身是有价值的产物：它显示「同一个问题往下挖三层会挖到什么」，
# 也让人看见自己每次都在同一个地方卡住。
MAX_DEPTH = 3


def chain_hint(depth):
    """第几层追问，给一句提示。超过上限就收，不要无限挖。"""
    if depth <= 0:
        return ""
    if depth >= MAX_DEPTH:
        return "已经挖到第三层了。面试里到这一层还没答清，对方基本会换题——但会在评估里记一笔。"
    return "第 %d 层追问" % depth
