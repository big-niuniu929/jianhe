# -*- coding: utf-8 -*-
"""
计费层 —— 会员 + 点数 + 真实成本计量
=====================================
设计目标：让「低成本、按量、可感知」这三个诉求同时成立。

先把成本结构摆清楚，因为定价必须从这里长出来：

    规则引擎（诊断/解析/JD匹配/岗位分析/坑库）  成本 ≈ 0
    大模型（改写/定制岗位分析/定制坑库）          成本 ≈ 0.02 元 / 次

这意味着一件事：**落笔的核心价值是零成本的，AI 只是增量。**
所以正确的收费结构不是「按 token 卖算力」（那是卖别人的东西），而是：

    零成本能力 → 免费，用来换口碑与传播（也是断网时的兜底）
    会员 4.9    → 卖「解锁 + 赠 20 点」，不承诺无限 AI
    点数        → 覆盖 AI 真实成本 + 固定倍率毛利

为什么按「点」不按「token」：
token 对用户是不可感知单位。用户无法预期"这次要花多少钱"，就会恐惧，
恐惧就不点。而「1 点 = 一次完整分析，还剩 47 点」是可预期的。
不可预期的计费，在低客单价产品里等于不收费。

为什么毛利用倍率而不是「3% 服务费」：
一次调用成本 0.018 元，3% 是 0.0005 元。月入 1 万需要流水 33 万元 token、
约 1800 万次调用、近百万用户。这条路在你有百万用户之前收入恒等于 0。
倍率（成本 × 3）在用户感知上和「几毛钱一次」没区别，但商业上差 20 倍。
详见 docs/定价与成本模型.md 的测算。
"""
import time

from . import store

# ---------------------------------------------------------------- 成本参数
# 大模型单价（元 / 百万 token）。**以官网价为准，改这里一处即可。**
# 这两行是唯一需要随供应商调价而修改的地方。
PRICE_IN_PER_MTOK = 2.0
PRICE_OUT_PER_MTOK = 8.0
MODEL_NAME = "deepseek-chat"

MARKUP = 3.0          # 售价 = 成本 × 3（毛利率 67%）
POINT_MICRO = 50_000  # 1 点 = 0.05 元 = 50000 微元

MEMBER_PRICE_FEN = 490     # 4.9 元
MEMBER_GIFT_POINTS = 20
FREE_EXPORTS = 3

PACKS = [
    {"key": "p100", "price": 5.0, "points": 100, "hot": False,
     "name": "试用包", "desc": "100 点，够一次完整秋招的材料打磨"},
    {"key": "p260", "price": 12.0, "points": 260, "hot": True,
     "name": "秋招包", "desc": "260 点，投 20–30 家够用，一岗一版"},
    {"key": "p700", "price": 29.0, "points": 700, "hot": False,
     "name": "全季包", "desc": "700 点，整季不数着用"},
]

MEMBER_BENEFITS = [
    "无水印导出，不限次数（导出本身不烧算力，所以敢放开）",
    "岗位知识库全解锁：8 个岗位族 / 96 条专业词 + 释义 + 用错会怎样",
    "实习坑库全解锁：同岗位的人踩过的坑 + 已经踩了怎么救",
    "赠 20 点，可体验 20 次 AI 深度能力",
    "后续新增岗位族免费更新",
]


def _fen(yuan):
    return int(round(float(yuan) * 100))


# ---------------------------------------------------------------- 钱包
def ensure(uid):
    if not uid:
        return None
    with store._conn() as con:
        con.execute("INSERT OR IGNORE INTO wallet(uid,points,member,paid_total,grant_total,updated_at) "
                    "VALUES(?,0,0,0,0,?)", (uid, int(time.time())))
        row = con.execute("SELECT * FROM wallet WHERE uid=?", (uid,)).fetchone()
    return dict(row) if row else None


def wallet(uid):
    return ensure(uid)


def is_member(uid):
    w = ensure(uid)
    return bool(w and w["member"])


def _uid_of(owner):
    return int(owner[2:]) if owner.startswith("u:") else None


# ---------------------------------------------------------------- 会员
def activate_member(uid, code=""):
    """开通会员。幂等：已是会员不重复扣款，直接返回。"""
    w = ensure(uid)
    if not w:
        return {"ok": False, "error": "no_user"}
    if w["member"]:
        return {"ok": True, "already": True, "points": w["points"], "member": True}
    now = int(time.time())
    with store._conn() as con:
        con.execute("UPDATE wallet SET member=1, member_at=?, member_code=?, "
                    "points=points+?, grant_total=grant_total+?, paid_total=paid_total+?, "
                    "updated_at=? WHERE uid=?",
                    (now, code, MEMBER_GIFT_POINTS, MEMBER_GIFT_POINTS,
                     MEMBER_PRICE_FEN, now, uid))
        con.execute("INSERT INTO topups(uid,cash_fen,points,kind,code,ts) VALUES(?,?,?,?,?,?)",
                    (uid, MEMBER_PRICE_FEN, MEMBER_GIFT_POINTS, "member", code, now))
        left = con.execute("SELECT points FROM wallet WHERE uid=?", (uid,)).fetchone()["points"]
    store.log(f"u:{uid}", "member_activate", {"code": code, "gift": MEMBER_GIFT_POINTS})
    return {"ok": True, "already": False, "points": left, "member": True,
            "gift": MEMBER_GIFT_POINTS}


# ---------------------------------------------------------------- 充值
def pack(key):
    for p in PACKS:
        if p["key"] == key:
            return p
    return None


def topup(uid, key, code=""):
    p = pack(key)
    if not p:
        return {"ok": False, "error": "没有这个套餐"}
    w = ensure(uid)
    if not w:
        return {"ok": False, "error": "no_user"}
    now = int(time.time())
    with store._conn() as con:
        con.execute("UPDATE wallet SET points=points+?, grant_total=grant_total+?, "
                    "paid_total=paid_total+?, updated_at=? WHERE uid=?",
                    (p["points"], p["points"], _fen(p["price"]), now, uid))
        con.execute("INSERT INTO topups(uid,cash_fen,points,kind,code,ts) VALUES(?,?,?,?,?,?)",
                    (uid, _fen(p["price"]), p["points"], key, code, now))
        left = con.execute("SELECT points FROM wallet WHERE uid=?", (uid,)).fetchone()["points"]
    store.log(f"u:{uid}", "topup", {"pack": key, "points": p["points"]})
    return {"ok": True, "points": left, "added": p["points"],
            "cash": p["price"], "kind": key}


# ---------------------------------------------------------------- 点数消费
def spend_points(uid, n, action="", detail=""):
    """
    扣点。返回 (ok, 余额, 原因)。
    三个原因码，前端据此分流：need_login / need_points / ok
    """
    if not uid:
        return False, 0, "need_login"
    n = max(1, int(n))
    ensure(uid)
    with store._conn() as con:
        row = con.execute("SELECT points FROM wallet WHERE uid=?", (uid,)).fetchone()
        if row["points"] < n:
            return False, row["points"], "need_points"
        con.execute("UPDATE wallet SET points=points-?, updated_at=? WHERE uid=?",
                    (n, int(time.time()), uid))
        left = con.execute("SELECT points FROM wallet WHERE uid=?", (uid,)).fetchone()["points"]
    return True, left, "ok"


# ---------------------------------------------------------------- 计量
def cost_micro(pt, ct):
    """真实成本（微元）。pt*单价 + ct*单价，因为单价已经是「元/百万 token」，
    乘完正好等于微元，不需要再除 1e6 —— 这个小巧合让全程可用整数。"""
    return int(round(int(pt or 0) * PRICE_IN_PER_MTOK + int(ct or 0) * PRICE_OUT_PER_MTOK))


def points_for(cost_micro_val):
    """成本 → 应扣点数。倍率后四舍五入，最少 1 点。"""
    charge = cost_micro_val * MARKUP
    return max(1, int(round(charge / POINT_MICRO)))


def estimate_points(pt=2000, ct=1800):
    """给前端显示「本次预计消耗」用的估算。"""
    return points_for(cost_micro(pt, ct))


def meter(owner, usage, action="", ok=True):
    """
    记一次 AI 调用。
    usage 是 OpenAI 兼容接口返回的 usage 字典；没有也能记（用估算值），
    因为有些代理/自建服务不返回 usage —— 宁可估算，也不要漏记。
    返回 {points, left, costYuan}；匿名用户返回 None（不扣不记）。
    """
    uid = _uid_of(owner)
    pt = int((usage or {}).get("prompt_tokens") or 0)
    ct = int((usage or {}).get("completion_tokens") or 0)
    if not pt and not ct and ok:
        pt, ct = 2000, 1800  # 兜底估算
    cm = cost_micro(pt, ct)
    n = points_for(cm) if ok else 0

    left = None
    if uid:
        if n:
            success, left, why = spend_points(uid, n, action)
            if not success:
                left = left
                n = 0  # 兜底：欠费的调用也记成本，但不扣点（避免负余额）
        w = ensure(uid)
        left = w["points"] if w else None

    with store._conn() as con:
        con.execute(
            "INSERT INTO calls(owner,action,model,pt,ct,cost_micro,charge_micro,charge_pt,ok,ts) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (owner, action, MODEL_NAME, pt, ct, cm,
             n * POINT_MICRO, n, 1 if ok else 0, int(time.time())))
    return {"points": n, "left": left, "costYuan": round(cm / 1e6, 4)}


# ---------------------------------------------------------------- 报表
def revenue(days=30):
    """
    后台用的真实账本。三个数必须同时看，否则会自欺：
      收入 = 预付现金（属于流水，不全是利润）
      成本 = 实际烧掉的算力（只算真发生的）
      负债 = 用户手里还没花掉的点数（这是你要交付的义务）
    """
    since = int(time.time()) - days * 86400
    with store._conn() as con:
        cash = con.execute("SELECT COALESCE(SUM(cash_fen),0) v FROM topups WHERE ts>=?",
                           (since,)).fetchone()["v"]
        calls = con.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(cost_micro),0) c, "
            "COALESCE(SUM(pt),0) pt, COALESCE(SUM(ct),0) ct FROM calls WHERE ts>=? AND ok=1",
            (since,)).fetchone()
        members = con.execute("SELECT COUNT(*) n FROM wallet WHERE member=1").fetchone()["n"]
        pts = con.execute("SELECT COALESCE(SUM(points),0) v FROM wallet").fetchone()["v"]
        paid_users = con.execute(
            "SELECT COUNT(DISTINCT uid) n FROM topups WHERE ts>=?", (since,)).fetchone()["n"]

    cash_yuan = cash / 100.0
    cost_yuan = calls["c"] / 1e6
    liability_yuan = pts * POINT_MICRO / 1e6
    return {
        "days": days,
        "cashYuan": round(cash_yuan, 2),
        "costYuan": round(cost_yuan, 2),
        "grossYuan": round(cash_yuan - cost_yuan, 2),
        "grossRate": round((cash_yuan - cost_yuan) / cash_yuan * 100, 1) if cash_yuan else None,
        "aiCalls": calls["n"],
        "tokens": {"in": calls["pt"], "out": calls["ct"]},
        "avgCostPerCall": round(cost_yuan / calls["n"], 4) if calls["n"] else 0,
        "members": members,
        "paidUsers": paid_users,
        "outstandingPoints": pts,
        "liabilityYuan": round(liability_yuan, 2),
        "note": "收入是预付现金，成本是实际消耗；未消耗点数（负债）不计入利润。",
    }


# ---------------------------------------------------------------- 给前端
def pricing():
    return {
        "currency": "CNY",
        "member": {
            "price": MEMBER_PRICE_FEN / 100.0,
            "gift": MEMBER_GIFT_POINTS,
            "benefits": MEMBER_BENEFITS,
            "note": "一次性开通。会员权益里没有「无限 AI」——AI 有真实成本，按点消耗对双方都公平。",
        },
        "packs": PACKS,
        "pointYuan": POINT_MICRO / 1e6,
        "freeExports": FREE_EXPORTS,
        "estimate": {"industry": estimate_points(2400, 2000),
                     "pitfall": estimate_points(1200, 1600),
                     "rewrite": estimate_points(1500, 900)},
        "priceTable": {"in": PRICE_IN_PER_MTOK, "out": PRICE_OUT_PER_MTOK,
                       "model": MODEL_NAME, "markup": MARKUP},
        "note": "点数 = AI 能力的计费单位。诊断、岗位库、坑库等规则能力永久免费，不扣点。",
    }
