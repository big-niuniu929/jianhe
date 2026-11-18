# -*- coding: utf-8 -*-
"""
落笔 · 服务端
=============
标准库实现，零第三方依赖。三层结构：
    web/    前端（纯静态）
    core/   引擎与业务（与 HTTP 无关，可被 CLI / FastAPI / 小程序复用）
    server  只做路由、鉴权、额度闸门

为什么坚持零依赖：要打包成 exe 发给同学、要 10 分钟部署到一台最低配云主机。
pip 依赖链在非技术用户机器上是最先崩的一环，而 core/ 与框架解耦，
将来流量起来要换 FastAPI + Postgres，把 core 原样搬过去即可。

启动：
    python server.py                    本地，127.0.0.1:8848
    JH_HOST=0.0.0.0 python server.py 80 服务器（公网）
    JH_ADMIN_KEY=xxx python server.py   指定运营后台密钥（不指定则随机生成并打印）
"""
import base64
import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import sys
import time
import zipfile
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from core import accounts
from core import billing
from core import contests as contests_mod
from core import industry as industry_mod
from core import jd as jd_mod
from core import jobmap as jobmap_mod
from core import lint as lint_mod
from core import llm as llm_mod
from core import interview as interview_mod
from core import drill as drill_mod
from core import metrics as metrics_mod
from core import parse as parse_mod
from core import pitfalls as pitfalls_mod
from core import prompts as prompts_mod
from core import rewrite as rewrite_mod
from core import store
from core import variants as variants_mod
from core.profile import normalize, new_profile, completeness

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
DATA = ROOT / "data"

# 版本号只在这里改一处。以前横幅、bat、README 三处各写各的，
# 结果 README 到 v1.3 了、启动横幅还印着 v1.1，用户会以为装的是旧版。
VERSION = "v1.3"

# 端口与绑定：三种跑法各有各的约定，别用一套默认值糊过去
#   1 本地双击「启动落笔.bat」→ 命令行传参 8848，只绑 127.0.0.1：简历不进局域网
#   2 Docker / 自备服务器  → JH_HOST=0.0.0.0、JH_PORT=8848
#   3 托管平台（含 WorkBuddy 发布应用）→ 平台注入 PORT，且**必须**绑 0.0.0.0，
#     否则反向代理连不上，表现为"启动失败"而不是代码错误，很难查
_ARG_PORT = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
_ENV_PORT = os.environ.get("PORT") or os.environ.get("JH_PORT")
PORT = _ARG_PORT or int(_ENV_PORT or 8848)
HOST = os.environ.get("JH_HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")


def _admin_key():
    """
    优先级：环境变量 > data/admin_key.txt（首次自动生成后复用）。
    每次重启都换 key 看起来安全，实际结果是没人记得住，最后写在便签上——
    不如落盘一次，让用户能把它存进密码管理器。
    """
    k = os.environ.get("JH_ADMIN_KEY")
    if k:
        return k, "env"
    f = DATA / "admin_key.txt"
    if f.exists():
        v = f.read_text(encoding="utf-8").strip()
        if v:
            return v, "file"
    v = secrets.token_urlsafe(18)
    DATA.mkdir(parents=True, exist_ok=True)
    f.write_text(v, encoding="utf-8")
    try:
        os.chmod(f, 0o600)
    except Exception:
        pass
    return v, "new"


ADMIN_KEY, ADMIN_SRC = _admin_key()

# 定价：改这里就是改价，前端从 /api/pricing 读，不硬编码在页面里
PRICING = billing.pricing()

# 社区帖子按求职流程分阶段。这几个键必须和 views2.js 里的 STAGE_LABEL 对齐，
# 否则筛选出来的空列表会被误读成"这个阶段没人发帖"。
COMMUNITY_STAGES = [
    {"key": "find", "label": "找实习时"},
    {"key": "onboard", "label": "入职前后"},
    {"key": "work", "label": "工作中"},
    {"key": "convert", "label": "转正环节"},
    {"key": "leave", "label": "结束时"},
]
# 一个帖子能多长。不限长会被灌水贴拖垮列表渲染，5000 字够写清楚一件事了。
POST_MAX = 5000
POST_TITLE_MAX = 60
# 每个账号每天能触发几次「AI 总结」的平台额度。
# 为什么是免费 + 限额而不是扣点：总结一次就被缓存在帖子上了，
# 之后所有人读这条都是免费的——平台的总成本被"帖子数"封顶，不随阅读量涨。
# 所以对读者按次收费没有道理，但完全不设限又会被批量刷。
SUMMARY_LIMIT = 20


def _day_start():
    """今天 0 点的时间戳。每日限额按自然日重置，不按滑动 24 小时——用户理解的是前者。"""
    t = time.localtime()
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))

SAMPLE_JD = """岗位：AI 产品运营（2027 届实习可转正）
工作内容：
1. 负责 AI 工具类产品的用户增长，围绕拉新、激活、留存设计运营动作；
2. 搭建数据看板与指标体系，用 SQL / Excel 做用户分层与漏斗分析；
3. 策划社群与内容活动，推动活动落地并复盘 ROI；
4. 与产品、算法、设计跨部门协作，把需求推进到上线。
任职要求：
1. 本科及以上，2027 届，每周到岗 4 天，实习期 3 个月以上；
2. 有较强的数据敏感度，熟练使用 Excel，会 SQL 优先；
3. 有内容运营或社群运营经验，能独立完成活动从策划到落地；
4. 抗压能力强，结果导向，接受快速迭代。
"""


def _json_bytes(obj, code=200):
    return code, json.dumps(obj, ensure_ascii=False).encode("utf-8")


def safe_url(raw):
    """
    只放行 http / https 的绝对地址。
    为什么必须白名单协议：这个值会被塞进 <a href>，如果放过 javascript:，
    用户（或别人贴给他的招聘链接）就能在自己的台账里存一条点一下就执行脚本的链接。
    相对路径也不放行 —— 台账里的链接一律是站外 JD，相对路径只可能是写错了。
    """
    u = (raw or "").strip()
    if not u:
        return ""
    if len(u) > 600:
        u = u[:600]
    low = u.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        # 用户常常只粘域名，补上协议头比报错友好
        if re.match(r"^[\w.-]+\.[a-z]{2,}(/|$|\?)", low):
            return "https://" + u
        return ""
    return u


def extract_text_from_upload(name, b64):
    """从 txt/md/docx 里取纯文本。PDF 走浏览器侧，服务端不引解析库。"""
    raw = base64.b64decode(b64.split(",")[-1])
    ext = (Path(name).suffix or "").lower()
    if ext in (".txt", ".md", ".markdown", ".text"):
        for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
            try:
                return raw.decode(enc), ""
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "ignore"), "编码识别失败，可能有乱码"
    if ext == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                xml = z.read("word/document.xml").decode("utf-8", "ignore")
            xml = re.sub(r"</w:p>", "\n", xml)
            xml = re.sub(r"<w:tab[^>]*/>", " ", xml)
            xml = re.sub(r"<[^>]+>", "", xml)
            txt = (xml.replace("&amp;", "&").replace("&lt;", "<")
                      .replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'"))
            lines = [x.strip() for x in txt.split("\n")]
            return "\n".join([x for x in lines if x]), ""
        except Exception as e:
            return "", f"docx 解析失败：{e}"
    if ext == ".pdf":
        return "", "PDF 请直接粘贴文字（浏览器里复制即可），或上传 .docx / .txt"
    return "", f"暂不支持 {ext or '该'} 格式，请上传 .docx / .txt，或直接粘贴文字"


class Handler(SimpleHTTPRequestHandler):
    server_version = "JianHe/1.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    # ------------------------------------------------------------ 基础
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Session")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        for c in getattr(self, "_cookies", []):
            self.send_header("Set-Cookie", c)
        self._cookies = []
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                # 浏览器刷新/关标签会把连接掐了，这不算错误，别刷一屏红字吓人
                pass

    def _set_cookie(self, name, value, days=30):
        c = f"{name}={value}; Path=/; Max-Age={days*86400}; HttpOnly; SameSite=Lax"
        self._cookies = getattr(self, "_cookies", []) + [c]

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _cookie(self, name):
        raw = self.headers.get("Cookie") or ""
        if not raw:
            return ""
        c = SimpleCookie()
        try:
            c.load(raw)
        except Exception:
            return ""
        return c[name].value if name in c else ""

    def _sid(self):
        raw = self.headers.get("X-Session") or ""
        if not raw:
            # 浏览器一定会带 X-Session；没带的是巡检脚本、爬虫、或在隐私模式下
            # 拿不到 localStorage 的用户。全都落到同一个 "anon" 上，等于把这些人
            # 塞进同一份简历里 —— 简历是最不能串味的东西。按 IP+UA 摊开。
            ip = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip() \
                or (self.client_address[0] if self.client_address else "")
            ua = self.headers.get("User-Agent", "")
            raw = "anon_" + hashlib.sha1((ip + "|" + ua).encode("utf-8")).hexdigest()[:12]
        return re.sub(r"[^A-Za-z0-9_\-]", "", raw)[:64] or "anon"

    def _uid(self):
        return accounts.uid_by_token(self._cookie("jh_tok"))

    def _owner(self):
        """一条数据只有一个归属。已登录用 u:id，否则用匿名会话。"""
        uid = self._uid()
        return f"u:{uid}" if uid else f"s:{self._sid()}"

    def _is_admin(self):
        key = self.headers.get("X-Admin-Key") or parse_qs(urlparse(self.path).query).get("key", [""])[0]
        return bool(key) and secrets.compare_digest(key, ADMIN_KEY)

    def do_OPTIONS(self):
        self._send(204, b"")

    # ------------------------------------------------------------ GET
    def do_GET(self):
        """
        GET 兜底。
        以前 do_GET 没有 try：任何异常都会逃出去，连接被直接关掉，客户端只看到
        「socket hang up」，既没有状态码也没有错误信息 —— 云端排查时等于全盲。
        这里统一转成 JSON 500，并把末行错误带回去（只在 500 时出现）。
        """
        try:
            return self._do_GET()
        except sqlite3.DatabaseError as e:
            # 库在运行期坏掉（最常见是云端残留的 -wal/-shm 和新主库对不上）。
            # 启动时的自愈只跑一次，这里再兜一次：重建后重放同一个请求，
            # 用户看到的是页面，而不是一张 500 错误页。
            try:
                store._recover_db()
            except Exception:
                pass
            try:
                return self._do_GET()
            except Exception as e2:
                import traceback
                traceback.print_exc()
                return self._send(*_json_bytes({
                    "ok": False,
                    "error": f"{type(e2).__name__}: {e2}",
                    "hint": "数据库已尝试自愈但仍不可用，请删除 data/jianhe.db 后重启",
                }, 500))
        except Exception as e:
            import traceback
            traceback.print_exc()
            tb = traceback.format_exc().strip().splitlines()
            return self._send(*_json_bytes({
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "where": " > ".join(x.strip() for x in tb[-4:-1]),
            }, 500))

    def _do_GET(self):
        u = urlparse(self.path)
        path = u.path

        if path in ("/", "/index.html"):
            f = WEB / "index.html"
            if f.exists():
                return self._send(200, f.read_bytes(), "text/html; charset=utf-8")
            return self._send(404, b"web/index.html missing", "text/plain")

        if path == "/api/boot":
            owner = self._owner()
            uid = self._uid()
            prof, is_sample = _profile_for(owner)
            store.log(owner, "visit", {"ua": self.headers.get("User-Agent", "")[:80]})
            return self._send(*_json_bytes({
                "profile": prof,
                "sampleJd": SAMPLE_JD,
                "llm": llm_mod.status(owner),
                "llmMine": llm_mod.mine(owner),
                "llmPresets": llm_mod.PRESETS,
                "completeness": completeness(prof),
                "isSample": is_sample,
                "user": accounts.get_user(uid),
                "credits": accounts.balance(owner),
                "wallet": billing.wallet(uid) if uid else None,
                "families": industry_mod.families(),
                "pitfallStats": pitfalls_mod.stats(),
                "pricing": PRICING,
                "variants": store.list_variants(owner),
                "anonymous": uid is None,
            }))

        if path == "/api/me":
            uid = self._uid()
            owner = self._owner()
            return self._send(*_json_bytes({
                "ok": True, "user": accounts.get_user(uid),
                "credits": accounts.balance(owner), "anonymous": uid is None,
                "wallet": billing.wallet(uid) if uid else None,
                "pricing": PRICING,
            }))

        if path == "/api/wallet":
            uid = self._uid()
            return self._send(*_json_bytes({
                "ok": True, "anonymous": uid is None,
                "wallet": billing.wallet(uid) if uid else None,
                "pricing": PRICING,
                "revenue": billing.revenue(30) if uid and self._is_admin() else None,
            }))

        if path == "/api/industry":
            # 规则版岗位分析：零成本，永久免费，不需要登录
            q = parse_qs(u.query)
            role = (q.get("role") or [""])[0]
            jd = (q.get("jd") or [""])[0]
            if not role and not jd:
                return self._send(*_json_bytes(
                    {"ok": False, "error": "给个岗位名或粘一段 JD"}, 400))
            res = self._industry_lookup(role, jd)
            store.log(self._owner(), "industry", {"family": res["family"], "mode": res["mode"]})
            return self._send(*_json_bytes({"ok": True, "result": res,
                                            "note": prompts_mod.MODE_NOTES["rules"]}))

        if path == "/api/pitfalls":
            q = parse_qs(u.query)
            fam = (q.get("family") or [""])[0]
            role = (q.get("role") or [""])[0]
            if not fam and role:
                fam = industry_mod.detect(role)[0]
            items = pitfalls_mod.list_for(fam or None, (q.get("q") or [""])[0],
                                          (q.get("stage") or [""])[0])
            return self._send(*_json_bytes({
                "ok": True, "family": fam or "common",
                "items": items, "checklist": pitfalls_mod.checklist(),
                "stats": pitfalls_mod.stats(),
                "note": prompts_mod.MODE_NOTES["rules"],
            }))

        if path == "/api/community":
            q = parse_qs(u.query)
            owner = self._owner()
            items = store.list_posts(
                (q.get("family") or [""])[0],
                (q.get("q") or [""])[0],
                (q.get("stage") or [""])[0],
                int((q.get("limit") or ["40"])[0] or 40),
                owner)
            # 每条帖子带上「是不是我写的」，前端才能决定显示不显示删除
            me = owner
            for it in items:
                it["mine"] = it.pop("owner", "") == me
            return self._send(*_json_bytes({
                "ok": True, "items": items,
                "stats": store.community_stats(),
                "anonymous": self._uid() is None,
                "stages": COMMUNITY_STAGES,
            }))

        if path == "/api/contests":
            q = parse_qs(u.query)
            return self._send(*_json_bytes({
                "ok": True,
                "items": contests_mod.list_all(
                    (q.get("cat") or [""])[0],
                    (q.get("fit") or [""])[0],
                    (q.get("q") or [""])[0]),
                "meta": contests_mod.meta(),
            }))

        if path == "/api/apps":
            return self._send(*_json_bytes({"items": store.list_apps(self._owner())}))
        if path == "/api/variants":
            return self._send(*_json_bytes(self._variants_payload(self._owner())))

        if path == "/api/pricing":
            return self._send(*_json_bytes({"ok": True, "pricing": PRICING}))

        if path == "/api/llm":
            owner = self._owner()
            uid = self._uid()
            plat = llm_mod.load_config()
            return self._send(*_json_bytes({
                "ok": True,
                "status": llm_mod.status(owner),
                "mine": llm_mod.mine(owner),
                "presets": llm_mod.PRESETS,
                "wallet": billing.wallet(uid) if uid else None,
                "platform": {"available": bool(plat.get("api_key")),
                             "model": plat.get("model") if plat.get("api_key") else None},
                "anonymous": uid is None,
            }))

        if path == "/api/stats":
            return self._send(*_json_bytes(store.stats_overview()))

        if path == "/api/health":
            return self._send(*_json_bytes({"ok": True, "llm": llm_mod.status()["mode"]}))

        # ---- 运营后台（一个只读页面 + 三个 JSON）----
        if path == "/admin":
            f = WEB / "admin.html"
            if self._is_admin() and f.exists():
                return self._send(200, f.read_bytes(), "text/html; charset=utf-8")
            return self._send(403, "需要管理密钥：/admin?key=xxx".encode("utf-8"), "text/plain; charset=utf-8")

        if path.startswith("/api/admin/"):
            if not self._is_admin():
                return self._send(*_json_bytes({"ok": False, "error": "无权限"}, 403))
            sub = path[len("/api/admin/"):]
            days = int(parse_qs(u.query).get("days", ["30"])[0] or 30)
            if sub == "overview":
                return self._send(*_json_bytes({"ok": True, "overview": metrics_mod.overview()}))
            if sub == "funnel":
                return self._send(*_json_bytes({"ok": True, "funnel": metrics_mod.funnel(days),
                                                "retention": metrics_mod.retention(days)}))
            if sub == "codes":
                return self._send(*_json_bytes({"ok": True, "items": accounts.list_codes()}))
            if sub == "users":
                return self._send(*_json_bytes({"ok": True, "items": accounts.list_users()}))
            if sub == "leads":
                return self._send(*_json_bytes({"ok": True, "items": accounts.list_leads()}))
            if sub == "revenue":
                return self._send(*_json_bytes({"ok": True, "revenue": billing.revenue(days),
                                                "pricing": PRICING}))
            if sub == "calls":
                return self._send(*_json_bytes({"ok": True, "items": store.recent_calls(200)}))

        # 静态文件
        rel = path.lstrip("/")
        target = (WEB / rel).resolve()
        if WEB.resolve() in target.parents and target.is_file():
            ctype = {
                ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".html": "text/html; charset=utf-8",
                ".svg": "image/svg+xml",
                ".png": "image/png",
                ".json": "application/json; charset=utf-8",
            }.get(target.suffix, "application/octet-stream")
            return self._send(200, target.read_bytes(), ctype)

        return self._send(404, b"not found", "text/plain")

    # ------------------------------------------------------------ POST
    def do_POST(self):
        path = urlparse(self.path).path
        owner = self._owner()
        sid = self._sid()
        body = self._body()

        try:
            # ---------------------------------------------- 账号
            if path == "/api/auth/register":
                uid = accounts.register(body.get("email"), body.get("pw"), body.get("name"))
                tok = accounts.issue_token(uid)
                self._set_cookie("jh_tok", tok)
                moved = store.migrate_owner(f"s:{sid}", f"u:{uid}")
                return self._send(*_json_bytes({
                    "ok": True, "user": accounts.get_user(uid),
                    "credits": accounts.balance(f"u:{uid}"), "migrated": moved,
                }))

            if path == "/api/auth/login":
                u = accounts.login(body.get("email"), body.get("pw"))
                store.migrate_owner(f"s:{sid}", f"u:{u['id']}")
                tok = accounts.issue_token(u["id"])
                self._set_cookie("jh_tok", tok)
                fresh = accounts.get_user(u["id"])
                store.log(f"u:{u['id']}", "login")
                return self._send(*_json_bytes({"ok": True, "user": fresh, "credits": fresh["credits"]}))

            if path == "/api/auth/logout":
                accounts.drop_token(self._cookie("jh_tok"))
                self._set_cookie("jh_tok", "", days=0)
                return self._send(*_json_bytes({"ok": True}))

            # ---------------------------------------------- 额度
            if path == "/api/redeem":
                add, left = accounts.redeem(body.get("code"), owner)
                return self._send(*_json_bytes({"ok": True, "added": add, "credits": left}))

            if path == "/api/lead":
                accounts.add_lead(body.get("contact"), body.get("intent", ""),
                                  body.get("note", ""), owner)
                return self._send(*_json_bytes({"ok": True, "msg": "收到了，我会在 12 小时内联系你"}))

            if path == "/api/events":
                for e in (body.get("events") or [])[:20]:
                    store.log(owner, str(e.get("name"))[:40], e.get("payload") or {})
                return self._send(*_json_bytes({"ok": True}))

            # ---------------------------------------------- 引擎
            if path == "/api/parse":
                text = body.get("text") or ""
                if not text and body.get("name"):
                    text, warn = extract_text_from_upload(body["name"], body.get("b64", ""))
                    if warn and not text:
                        store.log(owner, "parse", {"ok": False, "err": warn[:60]})
                        return self._send(*_json_bytes({"ok": False, "error": warn}, 400))
                if not text:
                    return self._send(*_json_bytes({"ok": False, "error": "没有可解析的内容"}, 400))
                res = parse_mod.parse_text(text)
                ok = bool(res["stats"].get("internships", 0) + res["stats"].get("projects", 0))
                store.log(owner, "parse", {"ok": ok, **res["stats"]})
                if ok:
                    store.log(owner, "import", {"chars": len(text)})
                return self._send(*_json_bytes({"ok": True, **res}))

            if path == "/api/save":
                prof = normalize(body.get("profile"))
                store.save_profile(owner, prof, body.get("score") or 0)
                # 一岗一版：改动同时写回当前版本，否则切走再切回就丢了
                store.touch_active_variant(owner, prof, score=body.get("score"))
                return self._send(*_json_bytes({"ok": True, "profile": prof,
                                                "completeness": completeness(prof)}))

            if path == "/api/lint":
                prof = normalize(body.get("profile"))
                res = lint_mod.lint(prof, body.get("jd") or "")
                store.save_profile(owner, prof, res["score"])
                store.touch_active_variant(owner, prof, score=res["score"])
                store.log(owner, "lint", {"score": res["score"], "issues": len(res["issues"])})
                return self._send(*_json_bytes({"ok": True, "result": res}))

            if path == "/api/jd":
                prof = normalize(body.get("profile"))
                res = jd_mod.match(prof, body.get("jd") or "")
                store.log(owner, "match", {"score": res["score"]})
                return self._send(*_json_bytes({"ok": True, "result": res}))

            if path == "/api/rewrite":
                text = (body.get("text") or "").strip()
                if not text:
                    return self._send(*_json_bytes({"ok": False, "error": "没有要改写的原文"}, 400))
                fn = llm_mod.callable_or_none(owner)
                if fn:
                    try:
                        versions, mode = rewrite_mod.rewrite_llm(
                            text, body.get("jdWords") or [], body.get("evidence") or "", fn)
                    except Exception as e:
                        versions, mode = rewrite_mod.rewrite_rules(
                            text, body.get("jdWords") or [], body.get("evidence") or ""), f"llm_error:{e}"
                else:
                    versions, mode = rewrite_mod.rewrite_rules(
                        text, body.get("jdWords") or [], body.get("evidence") or ""), "rules"
                store.log(owner, "rewrite", {"mode": mode})
                return self._send(*_json_bytes({"ok": True, "versions": versions, "mode": mode}))

            if path == "/api/interview":
                prof = normalize(body.get("profile"))
                j = body.get("jd") or ""
                m = jd_mod.match(prof, j)
                res = interview_mod.generate(prof, j, m["keywords"]["missing"])
                store.log(owner, "interview", {"n": res["total"]})
                return self._send(*_json_bytes({"ok": True, "result": res}))

            # 审一答：纯规则，不登录、不扣点。写答案是高频动作，不能收费。
            if path == "/api/drill/check":
                ans = (body.get("answer") or "").strip()
                if len(ans) > 6000:
                    ans = ans[:6000]
                res = drill_mod.analyze(ans, body.get("q") or "")
                return self._send(*_json_bytes({"ok": True, "result": res}))

            if path == "/api/apps":
                aid = store.add_app(owner, body.get("company", ""), body.get("role", ""),
                                    body.get("status", "待投"), body.get("deadline", ""),
                                    int(body.get("match") or 0), body.get("note", ""),
                                    safe_url(body.get("url")))
                return self._send(*_json_bytes({"ok": True, "id": aid, "items": store.list_apps(owner)}))

            # ---------------------------------------------- 一岗一版
            if path == "/api/variants":
                return self._variant_create(owner, body)
            if path == "/api/variants/activate":
                return self._variant_activate(owner, body)
            if path == "/api/variants/update":
                return self._variant_update(owner, body)
            if path == "/api/variants/delete":
                return self._variant_delete(owner, body)
            if path == "/api/variants/diff":
                return self._variant_diff(owner, body)

            # ---------------------------------------------- 社区（互助会）
            if path == "/api/community/post":
                return self._post_create(body)
            if path == "/api/community/like":
                return self._post_like(owner, body)
            if path == "/api/community/delete":
                return self._post_delete(owner, body)
            if path == "/api/community/summary":
                return self._post_summary(owner, body)

            # ---------------------------------------------- AI 深度能力（消耗点数）
            if path == "/api/industry/fill":
                # 地图外岗位补齐：不扣点、不拦登录，失败就当没发生
                return self._industry_fill(owner, body)

            if path == "/api/ai/industry":
                return self._ai_industry(owner, body)

            if path == "/api/tailor":
                return self._ai_tailor(owner, body)

            if path == "/api/audit":
                # 乱编检测：纯规则，零成本，不扣点、不需要登录
                text = (body.get("text") or "").strip()
                if not text:
                    return self._send(*_json_bytes({"ok": False, "error": "贴一段简历文字"}, 400))
                fam = body.get("family") or ""
                if not fam:
                    fam = industry_mod.detect((body.get("role") or "") + " " + text)[0]
                res = industry_mod.audit(text, fam)
                store.log(owner, "audit", {"family": fam, "n": res["riskCount"]})
                return self._send(*_json_bytes({"ok": True, "result": res}))

            if path == "/api/ai/pitfalls":
                return self._ai_pitfalls(owner, body)

            # ---------------------------------------------- 模型双轨（自带 Key）
            if path == "/api/llm/test":
                # 测试不要求登录：填了就能试，试通了再让他注册保存，
                # 否则用户得先注册才知道自己的 Key 能不能用，这个顺序很劝退
                prov = (body.get("provider") or "custom").strip()
                base = (body.get("base_url") or "").strip()
                model = (body.get("model") or "").strip()
                key = (body.get("api_key") or "").strip()
                if not key:
                    saved = llm_mod._user_cfg(owner)
                    if not saved:
                        return self._send(*_json_bytes(
                            {"ok": False, "error": "先填 Key，或者先在下面保存一次"}, 400))
                    cfg = dict(saved)
                    if base:
                        cfg["base_url"] = base
                    if model:
                        cfg["model"] = model
                else:
                    cfg = {"provider": prov, "base_url": base, "model": model,
                           "api_key": key, "timeout": 30}
                if not cfg.get("base_url") or not cfg.get("model"):
                    return self._send(*_json_bytes(
                        {"ok": False, "error": "接口地址和模型名要填"}, 400))
                res = llm_mod.test_cfg(cfg)
                if self._uid():
                    store.touch_llm_key(owner, res.get("ok"), res.get("error", ""))
                store.log(owner, "llm_test", {"ok": res.get("ok"), "model": cfg.get("model")})
                # 「Key 不对」是正常结果，不是请求失败 —— 一律 200 + passed，
                # 否则前端 api() 会把它当异常抛，错误详情就丢了
                return self._send(*_json_bytes({
                    "ok": True, "passed": bool(res.get("ok")),
                    "ms": res.get("ms"), "reply": res.get("reply"),
                    "tokens": res.get("tokens"), "model": cfg.get("model"),
                    "error": res.get("error") or "", "detail": res.get("detail") or "",
                }))

            if path == "/api/llm/key":
                if not self._uid():
                    return self._send(*_json_bytes(
                        {"ok": False, "code": "need_login",
                         "error": "Key 要存在账号上，先注册（10 秒）"}, 401))
                good, why = llm_mod.save_key(owner, body.get("provider"),
                                             body.get("base_url"), body.get("model"),
                                             body.get("api_key"))
                if not good:
                    return self._send(*_json_bytes({"ok": False, "error": why}, 400))
                store.log(owner, "llm_key_save", {"model": body.get("model")})
                return self._send(*_json_bytes({
                    "ok": True, "status": llm_mod.status(owner),
                    "mine": llm_mod.mine(owner), "msg": "已保存，之后 AI 调用走你自己的额度，不扣点"}))

            if path == "/api/llm/key/delete":
                if not self._uid():
                    return self._send(*_json_bytes({"ok": False, "code": "need_login"}, 401))
                llm_mod.clear_key(owner)
                return self._send(*_json_bytes({
                    "ok": True, "status": llm_mod.status(owner),
                    "msg": "已删除，回到平台额度（按量扣点）"}))

            # ---------------------------------------------- 会员与充值
            if path == "/api/member":
                uid = self._uid()
                if not uid:
                    return self._send(*_json_bytes(
                        {"ok": False, "error": "先注册，会员要记在账号上", "code": "need_login"}, 401))
                code = str(body.get("code") or "").strip()
                if not code:
                    return self._send(*_json_bytes(
                        {"ok": False, "error": "需要兑换码。付款后我会发给你", "code": "need_code"}, 400))
                res = accounts.redeem_member(code, uid)
                return self._send(*_json_bytes(dict(res, ok=True, wallet=billing.wallet(uid))))

            if path == "/api/topup":
                uid = self._uid()
                if not uid:
                    return self._send(*_json_bytes(
                        {"ok": False, "error": "先注册，点数要记在账号上", "code": "need_login"}, 401))
                code = str(body.get("code") or "").strip()
                if not code:
                    return self._send(*_json_bytes(
                        {"ok": False, "error": "需要充值码。付款后我会发给你", "code": "need_code"}, 400))
                res = accounts.redeem_topup(code, uid)
                return self._send(*_json_bytes(dict(res, ok=True, wallet=billing.wallet(uid))))

            # ---------------------------------------------- 出口闸门（唯一收费点）
            if path == "/api/export":
                return self._handle_export(owner, body)

            # ---------------------------------------------- 运营动作
            if path == "/api/admin/gencode":
                if not self._is_admin():
                    return self._send(*_json_bytes({"ok": False, "error": "无权限"}, 403))
                n = max(1, min(int(body.get("n") or 1), 50))
                credits = int(body.get("credits") or 1)
                kind = str(body.get("kind") or "single")[:8]
                note = str(body.get("note") or "")[:80]
                codes = [accounts.make_code(kind, credits, note) for _ in range(n)]
                return self._send(*_json_bytes({"ok": True, "codes": codes}))
        except accounts.AuthError as e:
            return self._send(*_json_bytes({"ok": False, "error": str(e)}, 400))
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self._send(*_json_bytes({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500))

        return self._send(404, b"not found", "text/plain")

    # ------------------------------------------------------------ 一岗一版
    def _variants_payload(self, owner, extra=None):
        items = store.list_variants(owner)
        act = next((v for v in items if v.get("active")), None)
        g = None
        if act:
            prof = store.get_profile(owner) or {}
            g = variants_mod.gap(prof, act.get("jd") or "")
        d = {"ok": True, "items": items, "activeId": act["id"] if act else None, "gap": g}
        if extra:
            d.update(extra)
        return d

    def _variant_create(self, owner, body):
        """新建一版：以当前档案为底，绑到一段新 JD 上。
        档案内容不动——先复制，再让用户按 tailer 的建议逐条改。"""
        prof = normalize(body["profile"]) if body.get("profile") else _profile_for(owner)[0]
        jd = (body.get("jd") or "").strip()
        roles = (prof.get("intent") or {}).get("targetRoles") or []
        role = (body.get("role") or "").strip() or variants_mod.guess_role(jd, roles[0] if roles else "")
        company = (body.get("company") or "").strip() or variants_mod.guess_company(jd)
        seq = len(store.list_variants(owner)) + 1
        name = (body.get("name") or "").strip() or variants_mod.compose_name(company, role, jd, seq)

        # 版本即岗位：绑定的岗位要同步进求职意向，否则简历标题还是上一家的岗位
        if role:
            prof.setdefault("intent", {})
            prof["intent"]["targetRoles"] = [role] + [r for r in roles if r != role][:2]

        g = variants_mod.gap(prof, jd)
        vid = store.add_variant(owner, name, role, company, jd, prof,
                                score=0, match=g["score"] or 0)
        store.save_profile(owner, prof, 0)
        res = lint_mod.lint(prof, jd)
        store.update_variant(owner, vid, score=res["score"])
        store.log(owner, "variant_new", {"name": name, "has_jd": bool(jd)})
        return self._send(*_json_bytes(self._variants_payload(owner, {
            "profile": prof, "lint": res, "id": vid,
            "match": jd_mod.match(prof, jd) if jd else None,
        })))

    def _variant_activate(self, owner, body):
        vid = int(body.get("id") or 0)
        v = store.activate_variant(owner, vid)
        if not v:
            return self._send(*_json_bytes({"ok": False, "error": "这一版不存在（可能已被删除）"}, 404))
        prof = normalize(v.get("profile"))
        store.save_profile(owner, prof, v.get("score") or 0)
        res = lint_mod.lint(prof, v.get("jd") or "")
        g = variants_mod.gap(prof, v.get("jd") or "")
        store.update_variant(owner, vid, score=res["score"], match=g["score"] or 0)
        store.log(owner, "variant_switch", {"name": v.get("name")})
        return self._send(*_json_bytes(self._variants_payload(owner, {
            "profile": prof, "lint": res, "match": jd_mod.match(prof, v.get("jd")) if v.get("jd") else None,
        })))

    def _variant_update(self, owner, body):
        vid = int(body.get("id") or 0)
        cur = store.get_variant(owner, vid)
        if not cur:
            return self._send(*_json_bytes({"ok": False, "error": "这一版不存在"}, 404))
        kw = {}
        for k in ("name", "role", "company", "jd", "note"):
            if k in body and body[k] is not None:
                kw[k] = str(body[k])[:4000]
        if "jd" in kw:
            kw["match"] = variants_mod.gap(cur["profile"], kw["jd"])["score"] or 0
        store.update_variant(owner, vid, **kw)
        return self._send(*_json_bytes(self._variants_payload(owner)))

    def _variant_delete(self, owner, body):
        vid = int(body.get("id") or 0)
        if not store.delete_variant(owner, vid):
            return self._send(*_json_bytes({"ok": False, "error": "这一版不存在"}, 404))
        act = store.active_variant(owner)
        extra = {}
        if act:
            prof = normalize(act.get("profile"))
            store.save_profile(owner, prof, act.get("score") or 0)
            extra = {"profile": prof, "lint": lint_mod.lint(prof, act.get("jd") or "")}
        store.log(owner, "variant_del", {"id": vid})
        return self._send(*_json_bytes(self._variants_payload(owner, extra)))

    def _variant_diff(self, owner, body):
        """对比两版。默认拿"最早那版"当基准 —— 那通常是用户的通用底版。"""
        vid = int(body.get("id") or 0)
        target = store.get_variant(owner, vid)
        if not target:
            return self._send(*_json_bytes({"ok": False, "error": "这一版不存在"}, 404))
        items = store.list_variants(owner)
        base = None
        aid = int(body.get("against") or 0)
        if aid:
            base = store.get_variant(owner, aid)
        else:
            others = [v for v in items if v["id"] != vid]
            if others:
                # 基准优先取"没绑 JD 的那一版"——那才是用户的通用底版；
                # 都绑了 JD 就取最早创建的，至少是个稳定的参照物。
                generic = [v for v in others if not (v.get("jd") or "").strip()]
                pick = min(generic or others, key=lambda x: x["id"])
                base = store.get_variant(owner, pick["id"])
        if not base:
            return self._send(*_json_bytes({"ok": True, "base": None,
                                            "diff": variants_mod.diff({}, target["profile"])}))
        return self._send(*_json_bytes({
            "ok": True,
            "base": {"id": base["id"], "name": base["name"]},
            "target": {"id": target["id"], "name": target["name"]},
            "diff": variants_mod.diff(base.get("profile"), target.get("profile")),
            "gap": variants_mod.plan(target.get("profile"), target.get("jd") or ""),
        }))

    # ------------------------------------------------------------ AI 深度能力
    # ------------------------------------------------------------ 社区
    def _display_name(self, uid):
        """
        帖子上显示的署名。
        已登录优先用用户自己填的名字；没有就用邮箱打码（h***@qq.com）。
        为什么不直接显示邮箱：社区是公共场所，邮箱露出去了就是一次泄漏。
        """
        u = accounts.get_user(uid) if uid else None
        if not u:
            return "匿名同学"
        name = (u.get("name") or "").strip()
        if name:
            return name[:16]
        em = (u.get("email") or "").strip()
        if "@" in em:
            head, _, tail = em.partition("@")
            keep = head[:1] if len(head) <= 3 else head[:2]
            return keep + "***@" + tail
        return "同学"

    def _post_create(self, body):
        uid = self._uid()
        if not uid:
            return self._send(*_json_bytes(
                {"ok": False, "code": "need_login",
                 "error": "发帖要留个名字，10 秒注册。看帖、搜帖、AI 总结都不用登录。"}, 401))
        title = (body.get("title") or "").strip()
        text = (body.get("body") or "").strip()
        if len(title) < 4:
            return self._send(*_json_bytes({"ok": False, "error": "标题至少 4 个字，方便别人搜到"}, 400))
        if len(text) < 20:
            return self._send(*_json_bytes(
                {"ok": False, "error": "正文至少 20 个字。这条帖子以后会被别的同学当依据，写清一点。"}, 400))
        if len(text) > POST_MAX:
            return self._send(*_json_bytes(
                {"ok": False, "error": "正文超过 %d 字了，分两条发更好读" % POST_MAX}, 400))

        role = (body.get("role") or "").strip()[:40]
        family = (body.get("family") or "").strip()
        if not family and role:
            family = industry_mod.detect(role)[0]
        stage = (body.get("stage") or "work").strip()
        if stage not in {s["key"] for s in COMMUNITY_STAGES}:
            stage = "work"
        tags = (body.get("tags") or "").strip()
        tags = ",".join([t.strip()[:12] for t in re.split(r"[,，\s]+", tags) if t.strip()][:6])

        pid = store.add_post(f"u:{uid}", self._display_name(uid), title[:POST_TITLE_MAX],
                             text, role, "" if family == "generic" else family,
                             stage, tags)
        store.log(f"u:{uid}", "post_create", {"id": pid, "family": family})
        return self._send(*_json_bytes({"ok": True, "id": pid,
                                        "items": store.list_posts(owner=f"u:{uid}")}))

    def _post_like(self, owner, body):
        try:
            pid = int(body.get("id") or 0)
        except Exception:
            pid = 0
        if not pid or not store.get_post(pid):
            return self._send(*_json_bytes({"ok": False, "error": "这条帖子不在了"}, 404))
        n = store.like_post(owner, pid, bool(body.get("on", True)))
        return self._send(*_json_bytes({"ok": True, "likes": n}))

    def _post_delete(self, owner, body):
        try:
            pid = int(body.get("id") or 0)
        except Exception:
            pid = 0
        p = store.get_post(pid)
        if not p:
            return self._send(*_json_bytes({"ok": False, "error": "这条帖子不在了"}, 404))
        if p.get("owner") != owner:
            return self._send(*_json_bytes(
                {"ok": False, "error": "只有作者能删自己的帖子"}, 403))
        store.delete_post(owner, pid)
        return self._send(*_json_bytes({"ok": True}))

    def _post_summary(self, owner, body):
        """
        给一条帖子做 AI 总结。
        免费但有前提：读（读缓存）永远免费；第一次生成要登录 + 每天有上限。
        理由写在 SUMMARY_LIMIT 的注释里——成本被帖子数封顶，不该按阅读收费。
        """
        try:
            pid = int(body.get("id") or 0)
        except Exception:
            pid = 0
        p = store.get_post(pid, owner)
        if not p:
            return self._send(*_json_bytes({"ok": False, "error": "这条帖子不在了"}, 404))

        cached = store.get_post_summary(pid)
        if cached:
            try:
                return self._send(*_json_bytes({"ok": True, "summary": json.loads(cached),
                                                "cached": True}))
            except Exception:
                pass  # 老数据不是 JSON，重新生成一遍

        cfg, source = llm_mod.cfg_for(owner)
        if not cfg:
            return self._send(*_json_bytes(
                {"ok": False, "code": "no_llm",
                 "error": "还没有可用的模型。接上之后可以给每条帖子做总结——总结一次，之后所有人免费读。"}, 200))
        uid = self._uid()
        if source == "platform":
            if not uid:
                return self._send(*_json_bytes(
                    {"ok": False, "code": "need_login",
                     "error": "生成总结要登录（用平台额度，不扣点）。已生成的总结谁都能免费看。"}, 200))
            n = store.count_events(owner, "post_summary", since=_day_start())
            if n >= SUMMARY_LIMIT:
                return self._send(*_json_bytes(
                    {"ok": False, "code": "quota",
                     "error": "今天的总结次数用完了（每天 %d 条）。填自己的 Key 不限次。" % SUMMARY_LIMIT}, 200))

        prompt = prompts_mod.build_post_summary(
            p.get("title"), p.get("body"), p.get("role"), p.get("stage"))
        try:
            content, usage = llm_mod.chat_usage(prompt, cfg=cfg)
            obj = prompts_mod.extract_json(content)
            summary = prompts_mod.coerce_post_summary(obj)
        except Exception as e:
            return self._send(*_json_bytes(
                {"ok": False, "code": "failed",
                 "error": "这次没总结成功（%s）。帖子内容不受影响。" % str(e)[:60]}, 200))

        store.set_post_summary(pid, json.dumps(summary, ensure_ascii=False))
        if source == "platform":
            store.log(owner, "post_summary", {"id": pid,
                                              "tokens": usage.get("total_tokens")})
        return self._send(*_json_bytes({"ok": True, "summary": summary, "cached": False}))

    def _industry_lookup(self, role, jd):
        """
        岗位分析的三级查找：
            1. 内置岗位族（11 个族 / 约 1300 个词条）—— 即时、免费、离线可用
            2. 地图外岗位的缓存（曾经被模型补过、全站复用）—— 即时、免费
            3. 都没有 → 返回 generic，并置 canFill，由前端**后台**去调模型
               为什么不在这个请求里同步调：现在是 30ms 出结果，同步调模型是 6–10 秒。
               为了一个用户不认识的岗位，把所有人的首屏都拖慢，不值。
        """
        res = industry_mod.analyze(role, jd)
        if res["family"].startswith("x_"):
            # 模型补出来的族，来源必须说清楚，不能冒充内置知识库
            res["source"] = "模型补齐的岗位族（已缓存，全站复用）"
            res["mapFilled"] = True
        if res["family"] != "generic":
            return res

        hit = jobmap_mod.from_cache(role)
        if hit:
            again = industry_mod.analyze(role, jd)
            if again["family"] != "generic":
                again["mapFilled"] = True
                again["cached"] = True
                return again
            # 缓存里有、但这条岗位名认不出来（比如用户只给了 JD 没给岗位名）
            # → 用族名再跑一次，别浪费这份缓存
            again = industry_mod.analyze(hit[1].get("label") or role, jd)
            if again["family"] != "generic":
                again["mapFilled"] = True
                again["cached"] = True
                return again

        res["canFill"] = bool(jobmap_mod.norm_key(role))
        return res

    def _industry_fill(self, owner, body):
        """
        地图外岗位的后台补齐。刻意**不走** _ai_guard：
        这里没有点数判断，也不该弹登录墙——它是个锦上添花的动作，
        失败就当没发生过，页面上的内置分析照旧可用。
        """
        role = (body.get("role") or "").strip()
        jd = (body.get("jd") or "").strip()
        uid = self._uid()
        r = jobmap_mod.fill(role, jd, owner, need_login=(uid is None))
        if not r.get("ok"):
            return self._send(*_json_bytes({"ok": False, "reason": r.get("reason"),
                                            "error": r.get("hint") or "这次没补上"}, 200))
        res = self._industry_lookup(role, jd)
        return self._send(*_json_bytes({
            "ok": True, "result": res, "cached": r.get("cached"),
            "source": r.get("source"), "uncertain": r.get("uncertain") or [],
            "note": "这个岗位不在内置地图里，已用模型补了一份并缓存，后面的人不用再等。",
        }))

    def _ai_guard(self, owner):
        """
        AI 调用的统一闸门。返回 (uid, cfg, source, err)。
        顺序刻意如此：先判登录，再判有没有模型，最后判点数 ——
        这样用户得到的提示永远是「下一步该做什么」，而不是笼统的失败。

        v1.5 双轨：source == 'user' 时用的是用户自己的 Key，
        不判点数、不扣点 —— 他花的是自己的钱，我们没理由再收一次。
        """
        uid = self._uid()
        if not uid:
            return None, None, None, _json_bytes(
                {"ok": False, "code": "need_login",
                 "error": "AI 能力要记在账号上，先花 10 秒注册"}, 401)
        cfg, source = llm_mod.cfg_for(owner)
        if not cfg:
            return None, None, None, _json_bytes(
                {"ok": False, "code": "no_llm",
                 "error": "还没接大模型。用内置知识库版本功能一样在，只是不针对你的 JD 定制"}, 503)
        if source == "user":
            return uid, cfg, source, None
        w = billing.wallet(uid)
        if not w or w["points"] < 1:
            return None, None, None, _json_bytes(
                {"ok": False, "code": "need_points",
                 "error": "点数用完了", "wallet": w, "pricing": PRICING}, 402)
        return uid, cfg, source, None

    def _ai_industry(self, owner, body):
        uid, cfg, source, err = self._ai_guard(owner)
        if err:
            return self._send(*err)

        role = (body.get("role") or "").strip()
        jd = (body.get("jd") or "").strip()
        if not role and not jd:
            return self._send(*_json_bytes({"ok": False, "error": "给个岗位名或粘一段 JD"}, 400))

        # 先用规则版兜底，AI 失败时用户不会看到空白
        base = industry_mod.analyze(role, jd)
        hint = ""
        prof = store.get_profile(owner)
        if prof:
            try:
                p = normalize(prof)
                bits = []
                for it in (p.get("internships") or [])[:2]:
                    bits.append(f"{it.get('org','')} {it.get('role','')}")
                for it in (p.get("projects") or [])[:1]:
                    bits.append(f"{it.get('name','')}")
                hint = "；".join(x for x in bits if x.strip())
            except Exception:
                hint = ""

        prompt = prompts_mod.build_industry(jd, role, hint)
        try:
            raw, usage = llm_mod.chat_usage(
                prompt, prompts_mod.SYS_INDUSTRY, temperature=0.25, cfg=cfg)
            obj = prompts_mod.extract_json(raw)
            res = prompts_mod.coerce_industry(obj)
            res["family"] = base["family"]
            # 规则库的细节题更准（人工挑过），保留它，AI 的只补空缺
            if len(res["probes"]) < 2:
                res["probes"] = base["probes"]
            if len(res["vocab"]) < 8:
                res["vocab"] = base["vocab"]
            res["jdVocab"] = base["jdVocab"]
            meter = billing.meter(owner, usage, "ai_industry", True) if source == "platform" else None
            store.log(owner, "ai_industry", {"family": base["family"], "src": source,
                                             "pt": usage.get("prompt_tokens")})
            return self._send(*_json_bytes({
                "ok": True, "result": res, "meter": meter, "source": source,
                "note": prompts_mod.MODE_NOTES["llm"]}))
        except Exception as e:
            if source == "platform":
                billing.meter(owner, None, "ai_industry", False)
            return self._send(*_json_bytes({
                "ok": True, "result": base, "mode": "fallback", "meter": None,
                "note": prompts_mod.MODE_NOTES["fallback"],
                "error": f"{type(e).__name__}: {e}"}))

    def _ai_pitfalls(self, owner, body):
        uid, cfg, source, err = self._ai_guard(owner)
        if err:
            return self._send(*err)

        role = (body.get("role") or "").strip()
        hint = (body.get("industry") or "").strip()
        if not role:
            return self._send(*_json_bytes({"ok": False, "error": "先填目标岗位"}, 400))

        fam = industry_mod.detect(role)[0]
        base_items = pitfalls_mod.list_for(fam if fam != "generic" else None)

        prompt = prompts_mod.build_pitfalls(role, hint)
        try:
            raw, usage = llm_mod.chat_usage(
                prompt, prompts_mod.SYS_PITFALL, temperature=0.35, cfg=cfg)
            obj = prompts_mod.extract_json(raw)
            ai_items = prompts_mod.coerce_pitfalls(obj, fam)
            if not ai_items:
                raise ValueError("模型没给出可用的坑")
            # AI 的排前面（针对性强），规则库的跟在后面（可信度高、经过筛选）
            seen = {x["title"][:8] for x in ai_items}
            merged = ai_items + [x for x in base_items if x["title"][:8] not in seen]
            meter = billing.meter(owner, usage, "ai_pitfalls", True) if source == "platform" else None
            store.log(owner, "ai_pitfalls", {"family": fam, "n": len(ai_items), "src": source})
            return self._send(*_json_bytes({
                "ok": True, "family": fam, "items": merged,
                "checklist": pitfalls_mod.checklist(),
                "stats": pitfalls_mod.stats(), "meter": meter, "source": source,
                "note": prompts_mod.MODE_NOTES["llm"]}))
        except Exception as e:
            if source == "platform":
                billing.meter(owner, None, "ai_pitfalls", False)
            return self._send(*_json_bytes({
                "ok": True, "family": fam, "items": base_items, "mode": "fallback",
                "checklist": pitfalls_mod.checklist(), "stats": pitfalls_mod.stats(),
                "note": prompts_mod.MODE_NOTES["fallback"],
                "error": f"{type(e).__name__}: {e}"}))

    # ------------------------------------------------------------ 按 JD 定制
    def _ai_tailor(self, owner, body):
        """
        一岗一版的引擎。这里刻意**不用** hard gate（401/402）：
        规则版定制是免费能力，不能因为没登录就整块消失。
        顺序：规则版先算出来（一定有结果）→ 够条件再试大模型 → 失败退回规则版且不扣点。
        付费点不是"能不能用"，而是"给不给你可直接采用的句子"。
        """
        prof = (normalize(body["profile"]) if body.get("profile")
                else _profile_for(owner)[0])
        jd = (body.get("jd") or "").strip()
        if not jd:
            return self._send(*_json_bytes(
                {"ok": False, "error": "这一版还没绑 JD。先贴岗位描述，才知道该往哪改。"}, 400))

        roles = (prof.get("intent") or {}).get("targetRoles") or []
        role = (body.get("role") or "").strip() or variants_mod.guess_role(jd, roles[0] if roles else "")
        company = (body.get("company") or "").strip() or variants_mod.guess_company(jd)

        m = jd_mod.match(prof, jd)
        missing = [k["word"] for k in m["keywords"]["missing"]]
        bullets = [{"bid": b["bid"], "text": b["text"], "label": b["label"]}
                   for b in variants_mod.doc_bullets(prof) if b["text"]]

        base = variants_mod.tailor_rules(prof, jd, missing)
        base["score"] = m["score"]
        base["missing"] = missing

        uid = self._uid()
        w = billing.wallet(uid) if uid else None
        tcfg, tsrc = llm_mod.cfg_for(owner)
        has_llm = bool(tcfg)
        if not uid:
            why = {"mode": "rules", "reason": "anonymous",
                   "msg": "登录后可以用 AI 版：它会直接给你能采用的句子，而不是只指出该改哪。"}
        elif not has_llm:
            why = {"mode": "rules", "reason": "no_llm",
                   "msg": "还没接大模型，先用规则版（免费）。接上后会给出可直接采用的句子。"}
        elif tsrc == "platform" and (not w or (w.get("points") or 0) < 1):
            # 自带 Key 的用户不判点数：这次调用花的是他自己的额度
            why = {"mode": "rules", "reason": "need_points",
                   "msg": "点数用完了。规则版仍然免费可用；充点后可以让 AI 直接改成可投的句子。"}
        else:
            why = None

        if why:
            base["gate"] = why
            return self._send(*_json_bytes({
                "ok": True, "result": base, "mode": "rules", "meter": None,
                "note": why["msg"]}))

        prompt = prompts_mod.build_tailor(role, company, jd, bullets, missing)
        try:
            raw, usage = llm_mod.chat_usage(prompt, prompts_mod.SYS_TAILOR,
                                            temperature=0.3, cfg=tcfg)
            obj = prompts_mod.extract_json(raw)
            res = prompts_mod.coerce_tailor(obj, [b["bid"] for b in bullets], missing)
            if not res["moves"]:
                raise ValueError("模型没给出可用条目")
            label = {b["bid"]: b["label"] for b in bullets}
            for mv in res["moves"]:
                mv["label"] = label.get(mv["bid"], "")
            res["score"] = m["score"]
            res["missing"] = missing
            meter = billing.meter(owner, usage, "ai_tailor", True) if tsrc == "platform" else None
            store.log(owner, "ai_tailor", {"moves": len(res["moves"]),
                                           "missing": len(missing), "src": tsrc})
            return self._send(*_json_bytes({
                "ok": True, "result": res, "mode": "llm", "meter": meter, "source": tsrc,
                "note": prompts_mod.MODE_NOTES["llm"]}))
        except Exception as e:
            if tsrc == "platform":
                billing.meter(owner, None, "ai_tailor", False)
            return self._send(*_json_bytes({
                "ok": True, "result": base, "mode": "fallback", "meter": None,
                "note": prompts_mod.MODE_NOTES["fallback"],
                "error": f"{type(e).__name__}: {e}"}))

    # ------------------------------------------------------------ 导出
    def _handle_export(self, owner, body):
        """
        四态返回，前端据此分流：
          401 need_login  → 弹注册（送免费次数，先给甜头）
          402 need_member → 弹会员（4.9，解锁无限制导出）
          200 member      → 会员直接放行，不扣次（导出不烧算力，敢放开）
          200 credit      → 非会员用免费次数
        """
        uid = self._uid()
        if not uid:
            store.log(owner, "gate", {"reason": "need_login"})
            return self._send(*_json_bytes(
                {"ok": False, "error": "导出前先建个账号（注册送免费次数）", "code": "need_login"}, 401))

        # 会员：不限次
        if billing.is_member(uid):
            self._bump_export(owner)
            store.log(owner, "export", {"member": True})
            return self._send(*_json_bytes({
                "ok": True, "member": True, "credits": accounts.balance(owner),
                "watermark": False, "mode": "print",
                "tip": "会员不限次导出。打印面板选「另存为 PDF」，文字层保留，ATS 可解析",
            }))

        ok, left, why = accounts.spend(owner, "export", 1, body.get("company") or "")
        if not ok:
            store.log(owner, "gate", {"reason": "need_member"})
            return self._send(*_json_bytes(
                {"ok": False, "error": "免费导出次数用完了。开通会员后不限次",
                 "code": "need_member", "credits": left, "pricing": PRICING}, 402))

        self._bump_export(owner)
        store.log(owner, "export", {"left": left})
        return self._send(*_json_bytes({
            "ok": True, "credits": left, "watermark": False,
            "mode": "print", "freeLeft": left,
            "tip": "已在浏览器打印面板选择「另存为 PDF」即可，文字层保留，ATS 可解析",
        }))

    def _bump_export(self, owner):
        """记一笔"这版投出去过"。版本的意义就是被投出去，不记这个数，版本列表就只是个文件柜。"""
        act = store.active_variant(owner)
        if act:
            store.update_variant(owner, act["id"], exports=(act.get("exports") or 0) + 1)

    # ------------------------------------------------------------ PATCH / DELETE
    def do_PATCH(self):
        path = urlparse(self.path).path
        m = re.match(r"^/api/apps/(\d+)$", path)
        if m:
            body = self._body()
            if "url" in body:
                body["url"] = safe_url(body.get("url"))
            store.update_app(self._owner(), int(m.group(1)), **body)
            return self._send(*_json_bytes({"ok": True, "items": store.list_apps(self._owner())}))
        return self._send(404, b"not found", "text/plain")

    def do_DELETE(self):
        path = urlparse(self.path).path
        m = re.match(r"^/api/apps/(\d+)$", path)
        if m:
            store.delete_app(self._owner(), int(m.group(1)))
            return self._send(*_json_bytes({"ok": True, "items": store.list_apps(self._owner())}))
        return self._send(404, b"not found", "text/plain")


def _sample_profile():
    f = DATA / "sample_profile.json"
    if f.exists():
        return normalize(json.loads(f.read_text(encoding="utf-8")))
    return new_profile()


def _profile_for(owner):
    """取用户档案；没有或等于空壳时回落到样例。

    判「有没有」不能只看记录是否存在：巡检请求、用户清空、异常写入都会留下
    一份空档案。那东西顶掉样例的结果，就是新访客打开一张白纸、50 分 D 档，
    他会以为站点坏了 —— 比给他一份能改的样例糟得多。
    """
    saved = store.get_profile(owner)
    if saved:
        cur = normalize(saved)
        if completeness(cur).get("score", 0) > 0:
            return cur, False
    return _sample_profile(), True


def main():
    print("=" * 60)
    print(f"  落笔 · 求职前置  {VERSION}")
    print(f"  站点：http://{HOST}:{PORT}")
    print(f"  引擎：{llm_mod.status()['note']}")
    print(f"  后台：http://{HOST}:{PORT}/admin?key={ADMIN_KEY}")
    if ADMIN_SRC == "new":
        print("        ^ 这个密钥已存到 data/admin_key.txt，下次启动不变，记得存进密码管理器")
    print("  Ctrl+C 停止")
    print("=" * 60)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
