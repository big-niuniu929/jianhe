# -*- coding: utf-8 -*-
"""
漏斗与留存 —— 创业项目唯一该天天盯的报表
========================================
比赛看的是"功能齐不齐"，创业看的是"人卡在哪一步走了"。
这个模块只算七个数，每一个都对应一个可执行动作：

  1. 访 → 导入      低 → 落地页没让人相信"这东西能改我这行"
  2. 导入 → 诊断    低 → 解析失败（技术问题，最该先修）
  3. 诊断 → 改写    低 → 诊断说了问题但没给下一步，价值感断裂
  4. 改写 → 导出    低 → 付费墙太靠前，用户还没被说服
  5. 导出 → 付费    低 → 9.9 太贵或免费版给太多
  6. 人均导出次数   → 决定做按次还是打包
  7. D1 / D7 回访   → 决定要不要做"投递台账"这种留人功能
"""
import time

from . import store

# 漏斗顺序（name, 中文名）
FUNNEL = [
    ("visit", "访问"),
    ("import", "导入简历"),
    ("lint", "看到诊断"),
    ("rewrite", "采纳改写"),
    ("gate", "撞到付费墙"),
    ("export", "导出成功"),
    ("paid", "付费/兑换"),
]


def funnel(days=30):
    """
    口径必须统一用「人数」——混用次数会算出 300% 这种自欺欺人的转化率。
    次数单独放在 times 里，只用于看人均行为（一个人导 5 次 vs 5 个人各 1 次，商业含义完全不同）。
    """
    since = int(time.time()) - days * 86400
    with store._conn() as con:
        rows = con.execute(
            "SELECT name, COUNT(DISTINCT owner) u, COUNT(*) c FROM events WHERE ts>=? GROUP BY name",
            (since,)).fetchall()
        paid_u = con.execute(
            "SELECT COUNT(DISTINCT owner) u, COUNT(*) c FROM usage "
            "WHERE action='redeem' AND ts>=?", (since,)).fetchone()
    m = {r["name"]: {"u": r["u"], "c": r["c"]} for r in rows}
    # 付费只算真实兑换（运营白送的不算，否则数据会好看但没意义）
    m["paid"] = {"u": paid_u["u"] or 0, "c": paid_u["c"] or 0}
    # 没跑过 gate 事件但成功导出的（老用户直接导出），保证漏斗单调不下穿
    if m.get("export", {}).get("u", 0) > m.get("gate", {}).get("u", 0):
        m["gate"] = {"u": m["export"]["u"], "c": max(m["gate"]["c"] if m.get("gate") else 0,
                                                    m["export"]["c"])}

    steps, prev = [], None
    for key, label in FUNNEL:
        cell = m.get(key, {"u": 0, "c": 0})
        n = cell["u"]
        # 用户会跳步（比如不诊断直接导出），此时人数会比上一步还多。
        # 硬算转化率会得到 -200% 这种数字，看着既错又误导，标记跳过即可。
        skipped = prev is not None and n > prev
        steps.append({
            "key": key,
            "label": label,
            "count": n,
            "times": cell["c"],
            "fromPrev": None if (prev is None or skipped) else round(n / prev * 100),
            "skipped": skipped,
            "fromTop": round(n / steps[0]["count"] * 100) if steps and steps[0]["count"] else None,
        })
        prev = max(prev or 0, n)
    return {"days": days, "steps": steps, "unit": "人数"}


def retention(days=30):
    """按"天"算回访：新 owner 首日建档，之后第 1/3/7 天是否再出现。"""
    since = int(time.time()) - days * 86400
    with store._conn() as con:
        rows = con.execute("SELECT owner, ts FROM events WHERE ts>=? ORDER BY ts", (since,)).fetchall()
    dayset = {}
    for r in rows:
        d = time.strftime("%Y-%m-%d", time.localtime(r["ts"]))
        dayset.setdefault(r["owner"], set()).add(d)
    def rate(n):
        cnt = sum(1 for s in dayset.values() if len(s) >= n)
        return round(cnt / len(dayset) * 100) if dayset else 0
    return {"owners": len(dayset), "d1": rate(2), "d3": rate(4), "d7": rate(8)}


def overview():
    """给运营看板的一页纸。所有数字都是现算的，不做缓存 —— 量小，别过早优化。"""
    now = int(time.time())
    day = now - 86400
    with store._conn() as con:
        q = lambda sql, *a: con.execute(sql, a).fetchone()[0]
        users_total = q("SELECT COUNT(*) FROM users")
        users_today = q("SELECT COUNT(*) FROM users WHERE created_at>=?", day)
        owners = q("SELECT COUNT(DISTINCT owner) FROM events WHERE ts>=?", day)
        profiles = q("SELECT COUNT(*) FROM profiles WHERE updated_at>=?", day)
        exports_total = q("SELECT COUNT(*) FROM usage WHERE action='export'")
        exports_today = q("SELECT COUNT(*) FROM usage WHERE action='export' AND ts>=?", day)
        credits_out = q("SELECT COALESCE(SUM(-cost),0) FROM usage WHERE cost<0")
        codes_made = q("SELECT COUNT(*) FROM codes")
        codes_used = q("SELECT COUNT(*) FROM codes WHERE used_by IS NOT NULL")
        leads = q("SELECT COUNT(*) FROM leads")
        avg_score = q("SELECT COALESCE(AVG(score),0) FROM profiles WHERE score>0")
        fail_parse = q("SELECT COALESCE(SUM(CASE WHEN payload LIKE '%\"ok\": false%' THEN 1 ELSE 0 END),0) FROM events WHERE name='parse'")
        parse_all = q("SELECT COUNT(*) FROM events WHERE name='parse'")
    return {
        "now": now,
        "users": {"total": users_total, "today": users_today},
        "active24h": owners,
        "profiles24h": profiles,
        "exports": {"total": exports_total, "today": exports_today},
        "avgScore": round(avg_score),
        "creditsIssued": credits_out,
        "codes": {"made": codes_made, "used": codes_used,
                  "rate": round(codes_used / codes_made * 100) if codes_made else 0},
        "leads": leads,
        "parseFailRate": round(fail_parse / parse_all * 100) if parse_all else 0,
    }
