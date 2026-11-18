# -*- coding: utf-8 -*-
"""
存储层 —— SQLite（零依赖，单文件，可备份、可迁移）
================================================
所有业务数据挂在 owner 上，owner 只有两种形态：
    u:<user_id>     已注册用户
    s:<session_id>  匿名会话（未注册）

为什么这样设计：匿名者必须先能完整用起来（否则一个登录墙就把转化率砍掉七成），
但所有数据从一开始就有归属，注册时把 s:xxx 整批迁移到 u:xxx 即可，
不需要"先体验后补录"那套丢数据的方案。

表：
  users      账号（邮箱 + PBKDF2 口令，不存明文）
  sessions   登录态 token
  codes      兑换码（MVP 期收钱用：微信收款 → 手工发码 → 用户自助激活）
  usage      每次消耗流水（谁、做了什么、扣了多少）
  profiles   档案快照
  applications 投递台账
  events     埋点（漏斗原料）
  leads      留资（没付钱但想要服务的人，比付费用户更值钱的前置信号）
"""
import json
import shutil
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jianhe.db"
SEED_PATH = DB_PATH.with_name("seed.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  pw TEXT NOT NULL,
  name TEXT DEFAULT '',
  credits INTEGER DEFAULT 3,
  created_at INTEGER,
  last_login INTEGER,
  note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  uid INTEGER NOT NULL,
  created_at INTEGER,
  last_seen INTEGER
);
CREATE TABLE IF NOT EXISTS codes (
  code TEXT PRIMARY KEY,
  kind TEXT DEFAULT 'trial',
  credits INTEGER DEFAULT 3,
  note TEXT DEFAULT '',
  created_at INTEGER,
  used_by INTEGER,
  used_at INTEGER
);
CREATE TABLE IF NOT EXISTS usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL,
  action TEXT NOT NULL,
  cost INTEGER DEFAULT 0,
  detail TEXT DEFAULT '',
  ts INTEGER
);
CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contact TEXT NOT NULL,
  intent TEXT DEFAULT '',
  note TEXT DEFAULT '',
  owner TEXT DEFAULT '',
  ts INTEGER
);
CREATE TABLE IF NOT EXISTS profiles (
  owner TEXT PRIMARY KEY,
  data TEXT NOT NULL,
  score INTEGER DEFAULT 0,
  updated_at INTEGER
);
-- v1.3 一岗一版：一条记录 = 一份"针对某个岗位的档案快照"
-- 为什么存整份快照而不是存 diff：切换必须 O(1)，而且任何一次改动都得能整体回退。
-- 存 diff 看着省空间，但一次改错会把两个版本一起弄脏，用户没法撤回。
CREATE TABLE IF NOT EXISTS variants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',   -- 显示名，如「字节跳动 · AI 产品运营」
  role TEXT DEFAULT '',
  company TEXT DEFAULT '',
  jd TEXT DEFAULT '',
  data TEXT NOT NULL,              -- 完整档案快照
  score INTEGER DEFAULT 0,         -- 该版本最近一次诊断得分
  match INTEGER DEFAULT 0,         -- 该版本对该 JD 的匹配度
  exports INTEGER DEFAULT 0,       -- 这版投出去过几次
  note TEXT DEFAULT '',
  active INTEGER DEFAULT 0,        -- 1 = 当前正在编辑的那一版
  created_at INTEGER,
  updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS applications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL,
  company TEXT, role TEXT, status TEXT, deadline TEXT,
  match INTEGER DEFAULT 0, note TEXT, created_at INTEGER,
  url TEXT DEFAULT ''                -- v1.5 岗位原链接（用户自己粘的）
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT, name TEXT, payload TEXT, ts INTEGER
);
-- v1.2 会员与点数钱包（独立表，避免 ALTER TABLE 在旧库上升级失败）
CREATE TABLE IF NOT EXISTS wallet (
  uid INTEGER PRIMARY KEY,
  points INTEGER DEFAULT 0,          -- 点数余额（AI 能力消耗）
  member INTEGER DEFAULT 0,          -- 1 = 创始会员
  member_at INTEGER,
  member_code TEXT DEFAULT '',
  paid_total INTEGER DEFAULT 0,      -- 累计付款（分），真实收入
  grant_total INTEGER DEFAULT 0,     -- 累计赠点（成本口径的负债）
  updated_at INTEGER
);
-- 每次 AI 调用的成本与售价，用于算真实毛利（单位：微元 1e-6 元，整数避免浮点误差）
CREATE TABLE IF NOT EXISTS calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT,
  action TEXT,
  model TEXT DEFAULT '',
  pt INTEGER DEFAULT 0,              -- prompt tokens
  ct INTEGER DEFAULT 0,              -- completion tokens
  cost_micro INTEGER DEFAULT 0,      -- 真实成本（微元）
  charge_micro INTEGER DEFAULT 0,    -- 向用户收取（微元）
  charge_pt INTEGER DEFAULT 0,       -- 扣了几个点
  ok INTEGER DEFAULT 1,
  ts INTEGER
);
CREATE TABLE IF NOT EXISTS topups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uid INTEGER,
  cash_fen INTEGER DEFAULT 0,        -- 实收（分）
  points INTEGER DEFAULT 0,          -- 到账点
  kind TEXT DEFAULT '',              -- member / pack100 / pack260 / pack700
  code TEXT DEFAULT '',
  ts INTEGER
);
-- v1.5 避坑社区：用户自己发的帖子。
-- 为什么作者名单独存一列而不是 JOIN users：帖子是公开内容，
-- 用户改名/注销后帖子里显示的名字不该跟着消失，也不该把邮箱漏出去。
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL,
  author TEXT DEFAULT '',            -- 显示名（昵称或脱敏邮箱）
  role TEXT DEFAULT '',              -- 岗位
  family TEXT DEFAULT '',            -- 岗位族（用于筛选）
  stage TEXT DEFAULT 'work',         -- 找实习/入职前后/工作中/转正/结束
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  tags TEXT DEFAULT '',              -- 逗号分隔
  likes INTEGER DEFAULT 0,
  summary TEXT DEFAULT '',           -- AI 总结缓存，避免同一条被反复总结
  summary_at INTEGER,
  hidden INTEGER DEFAULT 0,          -- 运营下架用
  created_at INTEGER
);
-- 岗位地图的模型补齐缓存：地图外的岗位由大模型生成一次，全站复用。
-- 这是「社区共建的知识库」在数据层的体现——第二个问同一个岗位的人不花钱。
CREATE TABLE IF NOT EXISTS jobmap_cache (
  key TEXT PRIMARY KEY,              -- 归一化后的岗位标识
  role TEXT DEFAULT '',
  data TEXT NOT NULL,                -- 与 industry.analyze() 同构的 JSON
  source TEXT DEFAULT 'llm',         -- llm / llm_reviewed
  hits INTEGER DEFAULT 0,
  created_at INTEGER
);
-- 用户自带的模型 Key（走自己的额度时不扣点）。
-- key_enc 不是明文：用服务端密钥派生的keystream 做过混淆，
-- 单机部署下这层等价于"别让 key 在 sqlite 文件里裸奔"，不是密码学意义上的加密。
CREATE TABLE IF NOT EXISTS llm_keys (
  owner TEXT PRIMARY KEY,
  base_url TEXT DEFAULT '',
  model TEXT DEFAULT '',
  provider TEXT DEFAULT '',
  key_enc TEXT DEFAULT '',
  key_tail TEXT DEFAULT '',          -- 只存后 4 位，用于回显
  created_at INTEGER,
  last_ok INTEGER,
  last_err TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_posts_fam ON posts(family);
-- 点赞单独一张表：posts.likes 做计数，这张表做"谁点过"。
-- 只有计数字段的话，同一个人能反复点，榜单立刻失真。
CREATE TABLE IF NOT EXISTS post_likes (
  post_id INTEGER NOT NULL,
  owner TEXT NOT NULL,
  ts INTEGER,
  PRIMARY KEY (post_id, owner)
);
CREATE INDEX IF NOT EXISTS idx_posts_ts ON posts(created_at);
CREATE INDEX IF NOT EXISTS idx_calls_ts ON calls(ts);
CREATE INDEX IF NOT EXISTS idx_var_owner ON variants(owner);
CREATE INDEX IF NOT EXISTS idx_app_owner ON applications(owner);
CREATE INDEX IF NOT EXISTS idx_evt_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_evt_owner ON events(owner);
CREATE INDEX IF NOT EXISTS idx_usage_owner ON usage(owner);
CREATE INDEX IF NOT EXISTS idx_sess_uid ON sessions(uid);
"""


def _db_ok(path=None):
    """能不能正常打开并读到元数据。用来判断主库是否可用。

    只用 quick_check 不够：页级损坏时 quick_check 可能仍然返回 ok，
    真正读表才炸（线上就踩过这个 —— 首页 /api/boot 直接 500）。
    所以这里再加三步：integrity_check + 读一次业务表 + 数一次页数。
    """
    p = path or DB_PATH
    try:
        con = sqlite3.connect(p, timeout=5)
        try:
            con.execute("SELECT count(*) FROM sqlite_master").fetchone()
            row = con.execute("PRAGMA integrity_check").fetchone()
            if not row or str(row[0]).lower() != "ok":
                return False
            for t in ("profiles", "posts", "applications"):
                try:
                    con.execute('SELECT count(*) FROM "%s"' % t).fetchone()
                except sqlite3.OperationalError:
                    pass          # 老库缺表不算损坏，_conn 会用 SCHEMA 补建
        finally:
            con.close()
        return True
    except Exception:
        return False


def _recover_db():
    """
    云端沙箱重发布只覆盖文件、不删多余文件：上一轮留下的 jianhe.db-wal /
    -shm 会和新上传的主库对不上，SQLite 直接判「database disk image is
    malformed」，所有读库接口一起 500。这里在启动时自愈，顺序是：
        ① 原样能开 → 不动（保留未回放的事务）
        ② 丢残留 -wal/-shm → 再试
        ③ 还坏 → 主库改名留证，用 seed.db 重建
    """
    if _db_ok():
        return
    for suf in ("-wal", "-shm"):
        p = Path(str(DB_PATH) + suf)
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass
    if _db_ok():
        return
    try:
        if DB_PATH.exists():
            DB_PATH.rename(DB_PATH.with_name("jianhe.corrupt-%d.db" % int(time.time())))
    except Exception:
        pass
    try:
        if SEED_PATH.exists():
            shutil.copyfile(SEED_PATH, DB_PATH)
        else:
            sqlite3.connect(DB_PATH).close()
    except Exception:
        pass


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        # 有的云端文件系统不支持 WAL，退回默认 journal 也能读写
        pass
    con.executescript(SCHEMA)
    _migrate(con)
    return con


try:
    _recover_db()
except Exception:
    pass


_MIGRATED = False


def _migrate(con):
    """
    老库加列。
    CREATE TABLE IF NOT EXISTS 对已存在的表完全不起作用，所以 v1.5 给
    applications 加 url 时必须单独 ALTER —— 不然老用户升级后
    「加网址」这个功能会静默失效（INSERT 报 no such column）。
    只跑一次：进程内用模块级开关锁住。
    """
    global _MIGRATED
    if _MIGRATED:
        return
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(applications)").fetchall()}
        if "url" not in cols:
            con.execute("ALTER TABLE applications ADD COLUMN url TEXT DEFAULT ''")
    except Exception:
        pass
    _MIGRATED = True


# ---------------------------------------------------------------- 档案
def save_profile(owner, profile, score=0):
    with _conn() as con:
        con.execute(
            "INSERT INTO profiles(owner,data,score,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(owner) DO UPDATE SET data=excluded.data, score=excluded.score, updated_at=excluded.updated_at",
            (owner, json.dumps(profile, ensure_ascii=False), score, int(time.time())),
        )


def get_profile(owner):
    with _conn() as con:
        row = con.execute("SELECT data FROM profiles WHERE owner=?", (owner,)).fetchone()
    return json.loads(row["data"]) if row else None


def migrate_owner(src, dst):
    """匿名 → 账号：把会话下的全部数据挂到用户名下，已有记录不覆盖（以 dst 为准）。"""
    if not src or not dst or src == dst:
        return 0
    n = 0
    with _conn() as con:
        cur = con.execute("UPDATE applications SET owner=? WHERE owner=?", (dst, src))
        n += cur.rowcount or 0
        cur = con.execute("UPDATE events SET owner=? WHERE owner=?", (dst, src))
        n += cur.rowcount or 0
        cur = con.execute("UPDATE usage SET owner=? WHERE owner=?", (dst, src))
        n += cur.rowcount or 0
        # 版本：账号侧已有版本时，把匿名侧的版本挪到账号下但不抢 active
        has_var = con.execute("SELECT 1 FROM variants WHERE owner=? LIMIT 1", (dst,)).fetchone()
        if has_var:
            cur = con.execute("UPDATE variants SET owner=?, active=0 WHERE owner=?", (dst, src))
        else:
            cur = con.execute("UPDATE variants SET owner=? WHERE owner=?", (dst, src))
        n += cur.rowcount or 0
        # 档案：账号侧已有真实档案时不覆盖
        has = con.execute("SELECT 1 FROM profiles WHERE owner=?", (dst,)).fetchone()
        if not has:
            cur = con.execute("UPDATE profiles SET owner=? WHERE owner=?", (dst, src))
            n += cur.rowcount or 0
    return n


# ---------------------------------------------------------------- 投递台账
def list_apps(owner):
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM applications WHERE owner=? ORDER BY (deadline='') , deadline ASC, id DESC", (owner,)
        ).fetchall()
    return [dict(r) for r in rows]


def add_app(owner, company, role, status="待投", deadline="", match=0, note="", url=""):
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO applications(owner,company,role,status,deadline,match,note,url,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (owner, company, role, status, deadline, match, note, url, int(time.time())),
        )
        return cur.lastrowid


def update_app(owner, app_id, **kw):
    fields = [k for k in ("company", "role", "status", "deadline", "match", "note", "url") if k in kw]
    if not fields:
        return False
    sql = "UPDATE applications SET " + ",".join(f"{f}=?" for f in fields) + " WHERE id=? AND owner=?"
    with _conn() as con:
        con.execute(sql, [kw[f] for f in fields] + [app_id, owner])
    return True


def delete_app(owner, app_id):
    with _conn() as con:
        con.execute("DELETE FROM applications WHERE id=? AND owner=?", (app_id, owner))
    return True


# ---------------------------------------------------------------- 版本（一岗一版）
VAR_FIELDS = ("name", "role", "company", "jd", "score", "match", "note", "exports")


def _var_row(r, with_data=True):
    if not r:
        return None
    d = dict(r)
    if with_data:
        d["profile"] = json.loads(d.pop("data") or "{}")
    else:
        d.pop("data", None)
    return d


def list_variants(owner):
    """列表不返回档案体：一次拉几十份简历会让首屏变慢，切换时再单独取。"""
    with _conn() as con:
        rows = con.execute(
            "SELECT id,owner,name,role,company,jd,score,match,exports,note,active,"
            "created_at,updated_at FROM variants WHERE owner=? "
            "ORDER BY active DESC, updated_at DESC, id DESC", (owner,)).fetchall()
    return [dict(r) for r in rows]


def get_variant(owner, vid):
    with _conn() as con:
        r = con.execute("SELECT * FROM variants WHERE id=? AND owner=?", (vid, owner)).fetchone()
    return _var_row(r)


def active_variant(owner):
    with _conn() as con:
        r = con.execute("SELECT * FROM variants WHERE owner=? AND active=1", (owner,)).fetchone()
    return _var_row(r)


def add_variant(owner, name, role="", company="", jd="", profile=None, score=0, match=0):
    now = int(time.time())
    with _conn() as con:
        con.execute("UPDATE variants SET active=0 WHERE owner=?", (owner,))
        cur = con.execute(
            "INSERT INTO variants(owner,name,role,company,jd,data,score,match,active,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,1,?,?)",
            (owner, name, role, company, jd,
             json.dumps(profile or {}, ensure_ascii=False), int(score or 0), int(match or 0), now, now))
        vid = cur.lastrowid
    return vid


def activate_variant(owner, vid):
    """切到某一版：返回该版完整数据；不存在则返回 None（不抛异常，调用方好兜）。"""
    v = get_variant(owner, vid)
    if not v:
        return None
    with _conn() as con:
        con.execute("UPDATE variants SET active=0 WHERE owner=?", (owner,))
        con.execute("UPDATE variants SET active=1 WHERE id=? AND owner=?", (vid, owner))
    v["active"] = 1
    return v


def update_variant(owner, vid, **kw):
    fields = [k for k in VAR_FIELDS if k in kw]
    if not fields:
        return False
    sql = "UPDATE variants SET " + ",".join(f"{f}=?" for f in fields) + ", updated_at=? WHERE id=? AND owner=?"
    with _conn() as con:
        con.execute(sql, [kw[f] for f in fields] + [int(time.time()), vid, owner])
    return True


def touch_active_variant(owner, profile, score=None, match=None):
    """
    编辑档案时顺手把当前版本一起更新。
    不做这一步的话，用户改完切走再切回来，改动就丢了——那是最伤信任的一类 bug。
    """
    now = int(time.time())
    sets = ["data=?", "updated_at=?"]
    vals = [json.dumps(profile or {}, ensure_ascii=False), now]
    if score is not None:
        sets.append("score=?")
        vals.append(int(score or 0))
    if match is not None:
        sets.append("match=?")
        vals.append(int(match or 0))
    with _conn() as con:
        cur = con.execute(
            "UPDATE variants SET " + ",".join(sets) + " WHERE owner=? AND active=1",
            vals + [owner])
        return cur.rowcount or 0


def delete_variant(owner, vid):
    """删版本。删的是当前版就把最近改过的那版设为当前，避免用户落到"无版本"状态。"""
    with _conn() as con:
        row = con.execute("SELECT active FROM variants WHERE id=? AND owner=?", (vid, owner)).fetchone()
        if not row:
            return False
        was_active = row["active"]
        con.execute("DELETE FROM variants WHERE id=? AND owner=?", (vid, owner))
        if was_active:
            nxt = con.execute(
                "SELECT id FROM variants WHERE owner=? ORDER BY updated_at DESC, id DESC LIMIT 1",
                (owner,)).fetchone()
            if nxt:
                con.execute("UPDATE variants SET active=1 WHERE id=?", (nxt["id"],))
    return True


# ---------------------------------------------------------------- 埋点
def log(owner, name, payload=None):
    try:
        with _conn() as con:
            con.execute("INSERT INTO events(owner,name,payload,ts) VALUES(?,?,?,?)",
                        (owner, name, json.dumps(payload or {}, ensure_ascii=False), int(time.time())))
    except Exception:
        pass


def count_events(owner, name, since=0):
    """数某个账号在某时间点之后触发了几次某事件。给每日限额用。"""
    try:
        with _conn() as con:
            r = con.execute("SELECT COUNT(*) c FROM events WHERE owner=? AND name=? AND ts>=?",
                            (owner, name, int(since))).fetchone()
        return int(r["c"] if r else 0)
    except Exception:
        return 0


def stats_overview():
    with _conn() as con:
        users = con.execute("SELECT COUNT(*) c FROM profiles").fetchone()["c"]
        avg = con.execute("SELECT AVG(score) s FROM profiles WHERE score>0").fetchone()["s"] or 0
        evts = {r["name"]: r["c"] for r in con.execute(
            "SELECT name, COUNT(*) c FROM events GROUP BY name").fetchall()}
    return {"users": users, "avgScore": round(avg), "events": evts}


# ---------------------------------------------------------------- AI 调用流水
def recent_calls(limit=200):
    """运营后台用：每次 AI 调用的真实成本与售价，用来核对毛利。"""
    with _conn() as con:
        rows = con.execute(
            "SELECT c.*, u.email FROM calls c LEFT JOIN users u "
            "ON u.id = CAST(REPLACE(c.owner,'u:','') AS INTEGER) "
            "WHERE c.owner LIKE 'u:%' ORDER BY c.id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["costYuan"] = round(d["cost_micro"] / 1e6, 4)
        d["chargeYuan"] = round(d["charge_micro"] / 1e6, 4)
        out.append(d)
    return out


# ---------------------------------------------------------------- 避坑社区
POST_FIELDS = ("role", "family", "stage", "title", "body", "tags", "hidden")


def _post_row(r):
    if not r:
        return None
    d = dict(r)
    d["tags"] = [t for t in (d.get("tags") or "").split(",") if t]
    # list_posts 的 SQL 不取 summary 全文（可能上千字），只取了一个 CASE 别名
    # hasSummary；get_post 取的是整行。两种来源都要能算出这个布尔值，
    # 所以这里不能只看 summary 字段——否则列表页永远显示"没有总结"。
    d["hasSummary"] = bool(d.get("summary")) or bool(d.get("hasSummary"))
    return d


def list_posts(family="", q="", stage="", limit=60, owner=""):
    """
    社区列表。按时间倒序。
    summary 只回 hasSummary 标记 + 正文，列表不返回总结全文（可能上千字，
    一屏 60 条会把首屏拖到几秒），点开某条再取。
    """
    sql = ("SELECT id,owner,author,role,family,stage,title,body,tags,likes,"
           "summary_at,hidden,created_at,"
           "CASE WHEN summary!='' THEN 1 ELSE 0 END AS hasSummary FROM posts WHERE hidden=0")
    args = []
    if family:
        sql += " AND (family=? OR family='')"
        args.append(family)
    if stage:
        sql += " AND stage=?"
        args.append(stage)
    if q:
        sql += " AND (title LIKE ? OR body LIKE ? OR tags LIKE ?)"
        like = "%" + q + "%"
        args += [like, like, like]
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    args.append(int(limit))
    with _conn() as con:
        rows = con.execute(sql, args).fetchall()
        liked = set()
        if owner:
            liked = {r["post_id"] for r in con.execute(
                "SELECT post_id FROM post_likes WHERE owner=?", (owner,)).fetchall()}
    out = []
    for r in rows:
        d = _post_row(r)
        d["liked"] = d["id"] in liked
        out.append(d)
    return out


def get_post(pid, owner=""):
    with _conn() as con:
        r = con.execute("SELECT * FROM posts WHERE id=? AND hidden=0", (pid,)).fetchone()
        liked = False
        if owner and r:
            liked = bool(con.execute("SELECT 1 FROM post_likes WHERE post_id=? AND owner=?",
                                     (pid, owner)).fetchone())
    d = _post_row(r)
    if d:
        d["liked"] = liked
    return d


def add_post(owner, author, title, body, role="", family="", stage="work", tags=""):
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO posts(owner,author,role,family,stage,title,body,tags,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (owner, author, role, family, stage, title, body,
             ",".join(tags) if isinstance(tags, (list, tuple)) else (tags or ""),
             int(time.time())))
        return cur.lastrowid


def delete_post(owner, pid):
    with _conn() as con:
        con.execute("DELETE FROM posts WHERE id=? AND owner=?", (pid, owner))
    return True


def like_post(owner, pid, on=True):
    """点赞幂等：同一 owner 对同一帖只算一次，取消则减回去。"""
    with _conn() as con:
        has = con.execute("SELECT 1 FROM post_likes WHERE post_id=? AND owner=?",
                          (pid, owner)).fetchone()
        if on and not has:
            con.execute("INSERT INTO post_likes(post_id,owner,ts) VALUES(?,?,?)",
                        (pid, owner, int(time.time())))
            con.execute("UPDATE posts SET likes=likes+1 WHERE id=?", (pid,))
        elif not on and has:
            con.execute("DELETE FROM post_likes WHERE post_id=? AND owner=?", (pid, owner))
            con.execute("UPDATE posts SET likes=MAX(0,likes-1) WHERE id=?", (pid,))
        row = con.execute("SELECT likes FROM posts WHERE id=?", (pid,)).fetchone()
    return row["likes"] if row else 0


def set_post_summary(pid, text):
    with _conn() as con:
        con.execute("UPDATE posts SET summary=?, summary_at=? WHERE id=?",
                    (text, int(time.time()), pid))


def get_post_summary(pid):
    with _conn() as con:
        r = con.execute("SELECT summary FROM posts WHERE id=?", (pid,)).fetchone()
    return (r["summary"] if r else "") or ""


def community_stats():
    with _conn() as con:
        n = con.execute("SELECT COUNT(*) c FROM posts WHERE hidden=0").fetchone()["c"]
        authors = con.execute("SELECT COUNT(DISTINCT owner) c FROM posts WHERE hidden=0").fetchone()["c"]
        fam = {r["family"]: r["c"] for r in con.execute(
            "SELECT family, COUNT(*) c FROM posts WHERE hidden=0 GROUP BY family").fetchall()}
    return {"posts": n, "authors": authors, "byFamily": fam}


# ---------------------------------------------------------------- 岗位地图补齐缓存
def jobmap_get(key):
    """命中就给，顺手记一次 hits —— 用来判断哪些岗位值得补进内置库。"""
    with _conn() as con:
        r = con.execute("SELECT * FROM jobmap_cache WHERE key=?", (key,)).fetchone()
        if r:
            con.execute("UPDATE jobmap_cache SET hits=hits+1 WHERE key=?", (key,))
    return dict(r) if r else None


def jobmap_put(key, role, data_json, source="llm"):
    with _conn() as con:
        con.execute(
            "INSERT INTO jobmap_cache(key,role,data,source,hits,created_at) VALUES(?,?,?,?,1,?) "
            "ON CONFLICT(key) DO UPDATE SET data=excluded.data, source=excluded.source",
            (key, role, data_json, source, int(time.time())))


def jobmap_top(limit=40):
    """运营后台：哪些岗位被问得最多、却还没进内置库。"""
    with _conn() as con:
        rows = con.execute(
            "SELECT key,role,hits,source,created_at FROM jobmap_cache "
            "ORDER BY hits DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- 用户自带模型 Key
def get_llm_key(owner):
    with _conn() as con:
        r = con.execute("SELECT * FROM llm_keys WHERE owner=?", (owner,)).fetchone()
    return dict(r) if r else None


def set_llm_key(owner, base_url, model, provider, key_enc, key_tail):
    now = int(time.time())
    with _conn() as con:
        con.execute(
            "INSERT INTO llm_keys(owner,base_url,model,provider,key_enc,key_tail,created_at) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(owner) DO UPDATE SET "
            "base_url=excluded.base_url, model=excluded.model, provider=excluded.provider, "
            "key_enc=excluded.key_enc, key_tail=excluded.key_tail, created_at=excluded.created_at, "
            "last_err=''",
            (owner, base_url, model, provider, key_enc, key_tail, now))


def touch_llm_key(owner, ok, err=""):
    with _conn() as con:
        con.execute("UPDATE llm_keys SET last_ok=?, last_err=? WHERE owner=?",
                    (int(time.time()) if ok else None, (err or "")[:200], owner))


def delete_llm_key(owner):
    with _conn() as con:
        con.execute("DELETE FROM llm_keys WHERE owner=?", (owner,))
    return True
