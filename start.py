# -*- coding: utf-8 -*-
"""OfferFit · 一键启动（轮询健康检查后再开浏览器，避免白屏）"""
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "app")
URL = "http://127.0.0.1:8848"
HEALTH = URL + "/api/health"


def healthy(timeout=0.8):
    try:
        with urllib.request.urlopen(HEALTH, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    if not healthy():
        print("启动服务中…")
        kwargs = {"cwd": APP}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8848"],
            **kwargs,
        )
        try:
            pidfile = os.path.join(APP, "data", "server.pid")
            os.makedirs(os.path.dirname(pidfile), exist_ok=True)
            with open(pidfile, "w", encoding="utf-8") as fh:
                fh.write(str(proc.pid))
        except Exception:
            pass
        for _ in range(60):
            time.sleep(0.5)
            if healthy():
                break
        else:
            print("服务启动失败：8848 被占用或依赖缺失。")
            print("排查：cd app 然后 python -m uvicorn main:app --port 8848 看报错。")
            input("按回车退出…")
            return
    print("已就绪：", URL)
    webbrowser.open(URL)


if __name__ == "__main__":
    main()
