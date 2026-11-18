# -*- coding: utf-8 -*-
"""
账号与额度 —— 创业产品的第一块地基
==================================
这里只做三件事，一件都不多做：

1. **身份**：邮箱 + 口令。PBKDF2-SHA256 加盐迭代 20 万次，标准库实现，不引第三方。
   不做短信验证码（成本 + 备案）、不做第三方登录（审核周期），
   因为冷启动阶段要验证的是"有没有人愿意为结果付钱"，不是"登录体验好不好"。
2. **额度**：一个 credits 整数。注册送 3 次导出，之后靠兑换码补。
   为什么按次不按订阅 —— 求职是低频高焦虑场景，9 月用完就走，
   订阅制在 10 月必然退订，退款率会吃掉全部收入。
3. **兑换码**：MVP 期收钱的正确姿势是「微信收款 → 手工发码 → 用户自助激活」。
   半小时能上线，零支付接口、零手续费、零合规风险，还能顺便完成一次真人接触。

定价（写在 billing.py 里，改一处即可）：
    单次导出 1 credit ≈ 9.9 元；5 次包 29 元；秋招全季 99 元。
"""
import hashlib
import re
import secrets
import time

from . import store

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
ITER = 200_000
SIGNUP_CREDITS = 3


class AuthError(Exception):
    pass


# ---------------------------------------------------------------- 口令
def hash_pw(pw):
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt.encode("utf-8"), ITER)
    return f"pbkdf2${ITER}${salt}${dk.hex()}"


def verify_pw(pw, stored):
    try:
        _, it, salt, h = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt.encode("utf-8"), int(it))
        return secrets.compare_digest(dk.hex(), h)
    except Exception:
        return False


def _norm_email(e):
    return (e or "").strip().lower()


# ---------------------------------------------------------------- 注册 / 登录
def register(email, pw, name=""):
    email = _norm_email(email)
    if not EMAIL_RE.match(email):
        raise AuthError("邮箱格式不对")
    if len(pw or "") < 6:
        raise AuthError("口令至少 6 位")
    now = int(time.time())
    with store._conn() as con:
        if con.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise AuthError("这个邮箱已经注册过了，直接登录")
        cur = con.execute(
            "INSERT INTO users(email,pw,name,credits,created_at,last_login) VALUES(?,?,?,?,?,?)",
            (email, hash_pw(pw), name or email.split("@")[0], SIGNUP_CREDITS, now, now))
        uid = cur.lastrowid
    store.log(f"u:{uid}", "signup", {"credits": SIGNUP_CREDITS})
    return uid


def login(email, pw):
    email = _norm_email(email)
    with store._conn() as con:
        row = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not row or not verify_pw(pw, row["pw"]):
            raise AuthError("邮箱或口令不对")
        con.execute("UPDATE users SET last_login=? WHERE id=?", (int(time.time()), row["id"]))
    return dict(row)


def issue_token(uid):
    tok = secrets.token_urlsafe(32)
    now = int(time.time())
    with store._conn() as con:
        con.execute("INSERT INTO sessions(token,uid,created_at,last_seen) VALUES(?,?,?,?)",
                    (tok, uid, now, now))
    return tok


def uid_by_token(tok):
    if not tok:
        return None
    with store._conn() as con:
        row = con.execute("SELECT uid FROM sessions WHERE token=?", (tok,)).fetchone()
        if not row:
            return None
        con.execute("UPDATE sessions SET last_seen=? WHERE token=?", (int(time.time()), tok))
    return row["uid"]


def drop_token(tok):
    if not tok:
        return
    with store._conn() as con:
        con.execute("DELETE FROM sessions WHERE token=?", (tok,))


def get_user(uid):
    if not uid:
        return None
    with store._conn() as con:
        row = con.execute("SELECT id,email,name,credits,created_at,last_login FROM users WHERE id=?",
                          (uid,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------- 额度
def balance(owner):
    """匿名用户也有额度概念（用于前端提示），但只有注册用户能真正扣。"""
    if owner.startswith("u:"):
        u = get_user(int(owner[2:]))
        return u["credits"] if u else 0
    return 0


def spend(owner, action, cost=1, detail=""):
    """扣额度。返回 (ok, 余额, 原因)。先扣后做，失败就回滚。"""
    if not owner.startswith("u:"):
        return False, 0, "need_login"
    uid = int(owner[2:])
    with store._conn() as con:
        row = con.execute("SELECT credits FROM users WHERE id=?", (uid,)).fetchone()
        if not row:
            return False, 0, "no_user"
        if row["credits"] < cost:
            return False, row["credits"], "no_credit"
        con.execute("UPDATE users SET credits=credits-? WHERE id=?", (cost, uid))
        con.execute("INSERT INTO usage(owner,action,cost,detail,ts) VALUES(?,?,?,?,?)",
                    (owner, action, cost, detail, int(time.time())))
        left = con.execute("SELECT credits FROM users WHERE id=?", (uid,)).fetchone()["credits"]
    return True, left, "ok"


def refund(owner, action):
    if not owner.startswith("u:"):
        return
    uid = int(owner[2:])
    with store._conn() as con:
        con.execute("UPDATE users SET credits=credits+1 WHERE id=?", (uid,))
        con.execute("INSERT INTO usage(owner,action,cost,detail,ts) VALUES(?,?,?,?,?)",
                    (owner, action + ":refund", -1, "失败回滚", int(time.time())))


def grant(owner, n, note=""):
    if not owner.startswith("u:"):
        return False
    uid = int(owner[2:])
    with store._conn() as con:
        con.execute("UPDATE users SET credits=credits+? WHERE id=?", (n, uid))
        con.execute("INSERT INTO usage(owner,action,cost,detail,ts) VALUES(?,?,?,?,?)",
                    (owner, "grant", -n, note, int(time.time())))
    return True


# ---------------------------------------------------------------- 兑换码
_CODE_ALPHABET = "ACDEFGHJKLMNPQRTUVWXY34679"  # 去掉易混字符


def make_code(kind="single", credits=1, note=""):
    body = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(12))
    code = f"{kind.upper()[:3]}-{body[:4]}-{body[4:8]}-{body[8:12]}"
    with store._conn() as con:
        con.execute("INSERT INTO codes(code,kind,credits,note,created_at) VALUES(?,?,?,?,?)",
                    (code, kind, credits, note, int(time.time())))
    return code


def _consume_code(code, uid):
    """
    校验并锁定兑换码。返回 (code_row, None) 或 (None, 错误响应)。
    用 `used_by IS NULL` 作为并发保护：两个人同时兑换，只有一个 UPDATE 会命中。
    """
    code = (code or "").strip().upper().replace(" ", "")
    if not code:
        raise AuthError("兑换码是空的")
    con = store._conn()
    try:
        row = con.execute("SELECT * FROM codes WHERE code=?", (code,)).fetchone()
        if not row:
            raise AuthError("兑换码不存在，检查一下有没有输错")
        if row["used_by"]:
            raise AuthError("这个码已经被用过了")
        cur = con.execute("UPDATE codes SET used_by=?, used_at=? WHERE code=? AND used_by IS NULL",
                          (uid, int(time.time()), code))
        if cur.rowcount == 0:
            raise AuthError("这个码刚被别人用掉了")
        con.commit()
        return dict(row)
    finally:
        con.close()


def redeem_member(code, uid):
    """会员码兑换。kind='member'。"""
    from . import billing  # 延迟导入，避免 accounts ↔ billing 循环
    row = _consume_code(code, uid)
    if row["kind"] != "member":
        # 用错了码：把码放回去，不要让用户损失
        with store._conn() as con:
            con.execute("UPDATE codes SET used_by=NULL, used_at=NULL WHERE code=?", (row["code"],))
        raise AuthError("这是充值码，不是会员码——去「充值」那里用")
    res = billing.activate_member(uid, row["code"])
    store.log(f"u:{uid}", "redeem_member", {"code": row["code"]})
    return res


def redeem_topup(code, uid):
    """充值码兑换。kind 必须是 billing.PACKS 里的 key。"""
    from . import billing
    row = _consume_code(code, uid)
    key = row["kind"]
    if key == "member":
        with store._conn() as con:
            con.execute("UPDATE codes SET used_by=NULL, used_at=NULL WHERE code=?", (row["code"],))
        raise AuthError("这是会员码，去「开通会员」那里用")
    if not billing.pack(key):
        with store._conn() as con:
            con.execute("UPDATE codes SET used_by=NULL, used_at=NULL WHERE code=?", (row["code"],))
        raise AuthError(f"未知的充值码类型：{key}")
    res = billing.topup(uid, key, row["code"])
    store.log(f"u:{uid}", "redeem_topup", {"code": row["code"], "pack": key})
    return res


def redeem(code, owner):
    if not owner.startswith("u:"):
        raise AuthError("先注册再兑换，额度要记在账号上")
    code = (code or "").strip().upper().replace(" ", "")
    uid = int(owner[2:])
    with store._conn() as con:
        row = con.execute("SELECT * FROM codes WHERE code=?", (code,)).fetchone()
        if not row:
            raise AuthError("兑换码不存在")
        if row["used_by"]:
            raise AuthError("这个码已经被用过了")
        con.execute("UPDATE codes SET used_by=?, used_at=? WHERE code=? AND used_by IS NULL",
                    (uid, int(time.time()), code))
        con.execute("UPDATE users SET credits=credits+? WHERE id=?", (row["credits"], uid))
        con.execute("INSERT INTO usage(owner,action,cost,detail,ts) VALUES(?,?,?,?,?)",
                    (owner, "redeem", -row["credits"], code, int(time.time())))
        left = con.execute("SELECT credits FROM users WHERE id=?", (uid,)).fetchone()["credits"]
    store.log(owner, "redeem", {"code": code, "credits": row["credits"]})
    return row["credits"], left


# ---------------------------------------------------------------- 留资
def add_lead(contact, intent="", note="", owner=""):
    contact = (contact or "").strip()
    if len(contact) < 3:
        raise AuthError("留个能联系上的方式（微信号 / 邮箱 / 手机号都行）")
    with store._conn() as con:
        con.execute("INSERT INTO leads(contact,intent,note,owner,ts) VALUES(?,?,?,?,?)",
                    (contact, intent, note, owner, int(time.time())))
    store.log(owner, "lead", {"intent": intent})
    return True


def list_leads(limit=200):
    with store._conn() as con:
        rows = con.execute("SELECT * FROM leads ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def list_codes(limit=200):
    with store._conn() as con:
        rows = con.execute("""
            SELECT c.*, u.email AS used_email FROM codes c
            LEFT JOIN users u ON u.id = c.used_by
            ORDER BY c.created_at DESC LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]


def list_users(limit=200):
    with store._conn() as con:
        rows = con.execute("""
            SELECT u.id,u.email,u.name,u.credits,u.created_at,u.last_login,
                   (SELECT COUNT(*) FROM usage x WHERE x.owner='u:'||u.id AND x.action='export') AS exports
            FROM users u ORDER BY u.id DESC LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]
