# -*- coding: utf-8 -*-
"""落笔 · 发布前自检（只读，不改任何数据）

回答三个问题：
1) 这个项目能不能在"平台注入 PORT"的方式下启动？—— 这是 A 方案唯一的真风险
2) 服务是不是真的绑在 0.0.0.0（而不是只有本机能连）？
3) 这次会上传哪些东西、有没有不该上传的？

用法：
    py -3 发布前自检.py

退出码：0 = 可以发布；1 = 有问题，看最后的"处置"
"""
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GREEN, RED, YELLOW, DIM, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ok_n = bad_n = warn_n = 0


def ok(msg, extra=""):
    global ok_n
    ok_n += 1
    print(f"  {GREEN}✔{OFF} {msg}" + (f"  {DIM}{extra}{OFF}" if extra else ""))


def bad(msg, extra=""):
    global bad_n
    bad_n += 1
    print(f"  {RED}✘{OFF} {msg}" + (f"  {DIM}{extra}{OFF}" if extra else ""))


def warn(msg, extra=""):
    global warn_n
    warn_n += 1
    print(f"  {YELLOW}!{OFF} {msg}" + (f"  {DIM}{extra}{OFF}" if extra else ""))


def head(t):
    print(f"\n{t}")


# ── 1. 环境 ─────────────────────────────────────────────
def check_env():
    head("一、运行环境")
    v = sys.version_info
    if v >= (3, 9):
        ok(f"Python {v.major}.{v.minor}.{v.micro}（要求 ≥ 3.9）")
    else:
        bad(f"Python {v.major}.{v.minor} 太旧，需要 ≥ 3.9")

    for f in ("server.py", "core/industry.py", "web/index.html", "data/jianhe.db"):
        p = ROOT / f
        (ok if p.exists() else bad)(f"存在 {f}")

    # 零依赖：扫 import 是否都在标准库内
    std = getattr(sys, "stdlib_module_names", None)
    if std:
        local = {"core", "server", "start"}
        third = set()
        for p in [ROOT / "server.py"] + list((ROOT / "core").glob("*.py")):
            txt = p.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", txt, re.M):
                top = m.group(1).split(".")[0]
                if top not in std and top not in local:
                    third.add(top)
        if third:
            bad("发现非标准库依赖", ", ".join(sorted(third)))
        else:
            ok("零第三方依赖（import 全在标准库内）")


# ── 2. 平台启动模拟 ──────────────────────────────────────
def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))   # 不会真的发包
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def hit(host, port, path="/api/health", timeout=1.0):
    try:
        with urllib.request.urlopen(f"http://{host}:{port}{path}", timeout=timeout) as r:
            return r.status, r.read(200).decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)


def check_boot():
    head("二、模拟云端启动（PORT 由环境变量注入）")
    port = free_port()
    env = dict(os.environ, PORT=str(port))
    env.pop("JH_HOST", None)          # 模拟平台：只给 PORT，不给 JH_HOST
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        [sys.executable, "server.py"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=flags,
    )
    code, body, log = None, "", ""
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            code, body = hit("127.0.0.1", port)
            if code == 200:
                break
            if proc.poll() is not None:
                break
            time.sleep(0.4)

        if code == 200:
            ok(f"PORT={port} 启动成功，/api/health 返回 200", body[:80].replace("\n", " "))
        else:
            log = (proc.stdout.read() or b"").decode("utf-8", "replace")[-1200:] if proc.stdout else ""
            bad(f"PORT={port} 启动失败（返回 {code}）")
            if log:
                print(f"    {DIM}--- 服务输出 ---{OFF}")
                for line in log.strip().splitlines()[-12:]:
                    print(f"    {DIM}{line}{OFF}")

        # 0.0.0.0 验证：用本机局域网 IP 连，只有绑 0.0.0.0 才通
        if code == 200:
            ip = lan_ip()
            if not ip:
                warn("拿不到本机局域网 IP，跳过 0.0.0.0 验证")
            elif hit(ip, port, timeout=1.5)[0] == 200:
                ok(f"绑定 0.0.0.0（从 {ip} 可访问，证明不是仅本机）")
            else:
                bad(f"只能从 127.0.0.1 访问，云端会 502", f"本机 IP {ip} 连不上")
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


# ── 3. 数据与上传内容 ───────────────────────────────────
def check_data():
    head("三、数据现状（决定要不要清）")
    try:
        import sqlite3
        c = sqlite3.connect(f"file:{ROOT/'data/jianhe.db'}?mode=ro", uri=True)
        rows = {t: c.execute(f'select count(*) from "{t}"').fetchone()[0]
                for (t,) in c.execute("select name from sqlite_master where type='table'")}
        c.close()
    except Exception as e:
        bad("读不了 data/jianhe.db", str(e))
        return
    dirty = {"profiles": "测试档案", "applications": "测试投递", "events": "埋点噪声"}
    for t, label in dirty.items():
        n = rows.get(t, 0)
        if n:
            warn(f"{t} {n} 条（{label}，建议发布前清空）")
        else:
            ok(f"{t} 已清空")
    for t, label in (("posts", "社区帖"), ("post_likes", "点赞")):
        n = rows.get(t, 0)
        (ok if n else warn)(f"{t} {n} 条（{label}，要保留{f'，否则页面会空' if t == 'posts' else ''}）")


RISKY = [
    ("data/admin_key.txt", "运营后台密钥，公开出去等于交出钥匙", "rm -f data/admin_key.txt"),
    ("data/.llmsecret", "自带 Key 的混淆密钥，不该随包公开", "rm -f data/.llmsecret"),
    ("docs", "商业策略/定价文档，不宜公开", "移到 归档/发布前移出/"),
    ("归档", "旧版文件，纯占体积", "移到项目外"),
    ("系统测试清单.html", "测试资产", "移到 归档/发布前移出/"),
    ("web/checklist.html", "测试资产，会被当静态页公开", "移到 归档/发布前移出/"),
    ("__pycache__", "字节码缓存，无用", "rm -rf __pycache__"),
    ("core/__pycache__", "字节码缓存，无用", "rm -rf core/__pycache__"),
    (".workbuddy", "会话日志", "保留本地，别上传"),
    ("data/jianhe.db-wal", "未落盘数据，服务停稳后应消失", "停服务后 rm"),
    ("data/jianhe.db-shm", "同上", "停服务后 rm"),
]


def check_upload():
    head("四、上传内容体检")
    for rel, why, fix in RISKY:
        p = ROOT / rel
        if p.exists():
            warn(f"{rel} 还在", f"{why} → {fix}")
        else:
            ok(f"{rel} 已处理")
    for p in ROOT.glob(".wbapp_*.genie"):
        warn(f"{p.name} 还在", "发布注册卡，别一起传上去 → 移到 归档/发布前移出/")

    total = 0
    big = []
    for p in ROOT.rglob("*"):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        s = p.stat().st_size
        total += s
        if s > 300 * 1024:
            big.append((s, p.relative_to(ROOT).as_posix()))
    mb = total / 1024 / 1024
    (ok if mb < 5 else warn)(f"目录总大小 {mb:.1f} MB", "含 docs/归档 时会虚高")
    for s, name in sorted(big, reverse=True)[:5]:
        warn(f"大文件 {name}", f"{s/1024:.0f} KB")


def main():
    print("=" * 62)
    print("  落笔 · 发布前自检（只读，不改数据）")
    print("=" * 62)
    check_env()
    check_boot()
    check_data()
    check_upload()

    print("\n" + "=" * 62)
    print(f"  通过 {ok_n} / 注意 {warn_n} / 失败 {bad_n}")
    if bad_n:
        print(f"  {RED}结论：先别发布，把上面标 ✘ 的处理掉。{OFF}")
    elif warn_n:
        print(f"  {YELLOW}结论：技术上可以发布；标「!」的是发布前的清理，建议做完再发。{OFF}")
    else:
        print(f"  {GREEN}结论：可以发布。{OFF}")
        print("  下一步：在对话里说「把这个项目发布成网站」。")
    print("=" * 62)
    return 0 if bad_n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
