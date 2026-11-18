# -*- coding: utf-8 -*-
"""落笔 · 启动器

解决两个老问题：
1) 浏览器抢在服务前面打开 → 页面"打不开"。这里等服务真的响应 200 再开浏览器。
2) 已经开着一个还再点一次 → 端口占用报错。这里先探测，已在运行就直接开浏览器。

用法：py -3 start.py [端口]
"""
import os
import sys
import time
import socket
import subprocess
import urllib.request
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8848
URL = "http://127.0.0.1:%d/" % PORT
WAIT = 25.0


def up(timeout=0.8):
    """服务是否真的能响应（不是只看端口通不通）"""
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "luobi-launcher"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def port_taken():
    s = socket.socket()
    s.settimeout(0.5)
    try:
        return s.connect_ex(("127.0.0.1", PORT)) == 0
    finally:
        s.close()


def banner(txt):
    print("  " + txt, flush=True)


def main():
    os.chdir(ROOT)

    # ── 已经在跑：不再启一个，直接开
    if up():
        banner("落笔已经在运行，正在打开浏览器…")
        banner(URL)
        webbrowser.open(URL)
        print()
        try:
            input("  按回车关闭本窗口（服务会继续运行）。")
        except (EOFError, OSError):
            pass
        return 0

    # ── 端口被别的程序占了
    if port_taken():
        banner("[!] 端口 %d 被别的程序占用了，落笔起不来。" % PORT)
        banner("    换个端口试试：在命令行里运行  py -3 start.py 8899")
        return 1

    # ── 启动
    banner("正在启动服务…（第一次大约 2 秒）")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    try:
        proc = subprocess.Popen(
            [sys.executable, "server.py", str(PORT)],
            cwd=ROOT, env=env,
        )
    except Exception as e:
        banner("[!] 调不起 python：%s" % e)
        return 1

    t0 = time.time()
    ok = False
    while time.time() - t0 < WAIT:
        if proc.poll() is not None:
            print()
            banner("[!] 服务启动失败（退出码 %s）。" % proc.returncode)
            banner("    上面若有红字报错，截图发我即可。")
            return 1
        if up():
            ok = True
            break
        time.sleep(0.2)

    if not ok:
        print()
        banner("[!] 等了 %d 秒服务还没响应。" % int(WAIT))
        banner("    可能卡在依赖或数据库锁，看看上面有没有报错。")
        return 1

    print()
    banner("=" * 44)
    banner("  落笔已就绪：" + URL)
    banner("  浏览器没自动打开的话，手动点上面这个地址。")
    banner("  关掉本窗口不会停服务；要停请双击『停止落笔.bat』。")
    banner("=" * 44)
    print(flush=True)
    webbrowser.open(URL)

    try:
        proc.wait()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
